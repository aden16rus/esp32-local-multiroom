"""
Multi-Room Arbitration Engine (Domain: Satellite).
Collects satellite INIT candidate messages over a sliding window and selects winner with highest RMS.
"""
import asyncio
import logging
from typing import List, Callable, Awaitable, Optional, Any
from server.domain.satellite.protocol import InitMessage, ControlMessage

logger = logging.getLogger("Arbiter")


class ArbiterCandidate:
    def __init__(self, init_msg: InitMessage, websocket_conn: Any):
        self.init_msg = init_msg
        self.ws = websocket_conn
        self.device_id = init_msg.device_id
        self.area_id = init_msg.area_id
        self.rms = init_msg.rms


class MultiRoomArbiter:
    def __init__(
        self,
        window_ms: float = 400.0,
        on_winner_callback: Optional[Callable[[ArbiterCandidate], Awaitable[None]]] = None,
        is_session_active_fn: Optional[Callable[[], bool]] = None
    ):
        self.window_seconds = window_ms / 1000.0
        self._pending_candidates: List[ArbiterCandidate] = []
        self._arbitration_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.on_winner_callback = on_winner_callback
        self.is_session_active_fn = is_session_active_fn

    async def register_candidate(
        self,
        init_msg: InitMessage,
        ws_conn: Any,
        callback: Optional[Callable[[ArbiterCandidate], Awaitable[None]]] = None
    ) -> None:
        """Register a satellite candidate. Rejects if session active; starts arbitration window timer if not running."""
        # 1. Active Session Busy Guard: Reject candidate if a pipeline session is already active
        if self.is_session_active_fn and self.is_session_active_fn():
            logger.info(f"Rejected candidate device_id='{init_msg.device_id}' - system busy with active session")
            try:
                stop_msg = ControlMessage(action="stop", reason="system_busy_active_session").to_json()
                await ws_conn.send(stop_msg)
            except Exception as e:
                logger.error(f"Failed to send busy stop to device_id='{init_msg.device_id}': {e}")
            return

        candidate = ArbiterCandidate(init_msg, ws_conn)
        async with self._lock:
            self._pending_candidates.append(candidate)
            logger.info(f"Registered candidate device_id='{candidate.device_id}' area='{candidate.area_id}' rms={candidate.rms:.2f}")

            if callback:
                self.on_winner_callback = callback

            if self._arbitration_task is None or self._arbitration_task.done():
                self._arbitration_task = asyncio.create_task(self._run_arbitration_window())

    async def _run_arbitration_window(self) -> None:
        """Wait for window duration then select candidate winner with max RMS."""
        await asyncio.sleep(self.window_seconds)

        async with self._lock:
            candidates = list(self._pending_candidates)
            self._pending_candidates.clear()
            self._arbitration_task = None

        if not candidates:
            return

        winner = max(candidates, key=lambda c: c.rms)
        logger.info(f"Arbitration complete. Winner: device_id='{winner.device_id}' area='{winner.area_id}' rms={winner.rms:.2f}")

        if self.on_winner_callback:
            try:
                await self.on_winner_callback(winner)
            except Exception as e:
                logger.error(f"Error executing on_winner_callback: {e}")

        for c in candidates:
            try:
                if c is winner or c.ws == winner.ws:
                    control_frame = ControlMessage(action="continue").to_json()
                    await c.ws.send(control_frame)
                    logger.debug(f"Sent 'continue' to winner device_id='{c.device_id}'")
                else:
                    control_frame = ControlMessage(action="stop", reason=f"RMS lower than winner ({c.rms:.1f} < {winner.rms:.1f})").to_json()
                    await c.ws.send(control_frame)
                    logger.debug(f"Sent 'stop' to rejected device_id='{c.device_id}'")
            except Exception as e:
                logger.error(f"Failed to send control frame to device_id='{c.device_id}': {e}")
