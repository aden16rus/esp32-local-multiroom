"""
Satellite Simulator Client.
Simulates ESP32-S3 satellite connection, wake word trigger, arbitration, PCM audio streaming, and playback.
"""
import asyncio
import json
import logging
import numpy as np
import websockets
from typing import Optional, Dict, Any

logger = logging.getLogger("SatelliteSim")


class SimulatedSatellite:
    def __init__(self, device_id: str, area_id: str, server_url: str = "ws://127.0.0.1:8765"):
        self.device_id = device_id
        self.area_id = area_id
        self.server_url = server_url
        self.state = "IDLE"
        self.received_audio_bytes = bytearray()

    async def trigger_wake_word(self, rms: float, mock_speech: bool = True) -> str:
        """
        Simulate wake word trigger:
        1. Connect to WS
        2. Send INIT payload with device_id, area_id, rms
        3. Await CONTROL payload (continue/stop)
        4. If continue, stream PCM audio frames
        5. Receive playback audio stream from server
        """
        self.state = "INIT"
        self.received_audio_bytes.clear()

        async with websockets.connect(self.server_url) as ws:
            # Send INIT frame
            init_payload = {
                "event": "init",
                "device_id": self.device_id,
                "area_id": self.area_id,
                "rms": rms
            }
            await ws.send(json.dumps(init_payload))
            logger.info(f"[{self.device_id}] Sent INIT with RMS={rms:.1f}")

            # Wait for CONTROL frame
            ctrl_msg = await ws.recv()
            ctrl_data = json.loads(ctrl_msg)
            action = ctrl_data.get("action")
            logger.info(f"[{self.device_id}] Received control action: '{action}'")

            if action == "stop":
                self.state = "IDLE"
                return "STOP"

            if action == "continue":
                self.state = "STREAMING"

                # Send audio frames (300 ms speech audio + 800 ms silence to trigger VAD)
                if mock_speech:
                    # Speech tone frame (high energy)
                    t_speech = np.linspace(0, 0.4, int(16000 * 0.4))
                    speech_int16 = (np.sin(2 * np.pi * 440 * t_speech) * 16000).astype(np.int16)
                    
                    # Silence frame
                    silence_int16 = np.zeros(int(16000 * 0.8), dtype=np.int16)

                    pcm_signal = np.concatenate([speech_int16, silence_int16]).tobytes()

                    # Stream in 100ms chunks (3200 bytes)
                    chunk_size = 3200
                    for i in range(0, len(pcm_signal), chunk_size):
                        chunk = pcm_signal[i:i+chunk_size]
                        await ws.send(chunk)
                        await asyncio.sleep(0.05)

                # Transition to playback receiving state
                self.state = "PLAYBACK"
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        if isinstance(msg, bytes):
                            self.received_audio_bytes.extend(msg)
                except asyncio.TimeoutError:
                    logger.info(f"[{self.device_id}] Finished receiving playback audio ({len(self.received_audio_bytes)} bytes)")

                self.state = "IDLE"
                return "COMPLETED"

        return "UNKNOWN"
