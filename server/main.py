"""
Central Server Entry Point for Local Voice Control System with Web UI & Audio Diagnostics.
Integrates WebSockets for ESP32-S3 Satellites and HTTP/WebSocket Web Dashboard on Port 8765.
"""
import os
import json
import base64
import logging
import asyncio
from aiohttp import web, WSMsgType
from typing import Dict, Set, Any, Optional

from server.domain.satellite import (
    parse_incoming_message,
    InitMessage,
    ControlMessage,
    ConfigUpdateMessage,
    MultiRoomArbiter,
    ArbiterCandidate,
    SatellitePipelineSession
)
from server.domain.homeassistant import HomeAssistantClient
from server.domain.audio import SherpaSTTEngine, PiperTTSEngine, AudioStore, AudioCleaner, RECORDINGS_DIR

from server.config import ServerConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("VoiceServer")

WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "web"))


class AppRunnerAdapter:
    def __init__(self, runner: web.AppRunner, ha_client: HomeAssistantClient):
        self._runner = runner
        self._ha_client = ha_client

    def close(self):
        try:
            loop = asyncio.get_running_loop()
            return loop.create_task(self.cleanup())
        except RuntimeError:
            asyncio.run(self.cleanup())

    async def wait_closed(self):
        pass

    async def cleanup(self):
        await self._runner.cleanup()
        await self._ha_client.close()

    def __await__(self):
        return self.cleanup().__await__()


class CentralVoiceServer:
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        ha_url: Optional[str] = None,
        config: Optional[ServerConfig] = None
    ):
        self.config = config or ServerConfig.from_env()
        from server.config import setup_logging
        setup_logging(log_level=self.config.log_level, log_file=self.config.log_file)

        self.host = host if host is not None else self.config.host
        self.port = port if port is not None else self.config.port
        target_ha_url = ha_url if ha_url is not None else self.config.ha_url

        self.last_session_end_time = 0.0
        self.arbiter = MultiRoomArbiter(
            window_ms=self.config.arbiter_window_ms,
            on_winner_callback=self._on_arbiter_winner,
            is_session_active_fn=self.is_session_active
        )
        self.ha_client = HomeAssistantClient(url=target_ha_url, token=self.config.ha_token, timeout=self.config.ha_timeout)
        self.stt_engine = SherpaSTTEngine()
        self.tts_engine = PiperTTSEngine()
        self.audio_store = AudioStore()
        self.audio_cleaner = AudioCleaner(
            sample_rate=16000,
            denoise_enabled=self.config.denoise_enabled,
            noise_suppression_level=self.config.noise_suppression_level,
            gain_multiplier=self.config.gain_multiplier,
            target_speech_rms=self.config.agc_target_rms,
            hpf_cutoff_hz=self.config.hpf_cutoff_hz,
            agc_max_gain=self.config.agc_max_gain
        )

        # Active satellite sessions keyed by WebSocket connection
        self.active_sessions: Dict[Any, SatellitePipelineSession] = {}
        
        # Connected satellite devices info (device_id -> info dict)
        self.connected_satellites: Dict[str, Dict[str, Any]] = {}
        self.ws_to_device_id: Dict[Any, str] = {}

        # Web UI browser WebSocket clients
        self.web_ui_clients: Set[web.WebSocketResponse] = set()

    def is_session_active(self) -> bool:
        """Return True if any satellite session is currently active or within post-session lock window."""
        if len(self.active_sessions) > 0:
            return True
        import time
        cooldown = getattr(self.config, "arbiter_cooldown_sec", 1.5)
        if (time.time() - self.last_session_end_time) < cooldown:
            return True
        return False

    async def _on_arbiter_winner(self, winner: ArbiterCandidate):
        logger.info(f"Creating active session for winner device '{winner.device_id}'")
        session = SatellitePipelineSession(
            device_id=winner.device_id,
            area_id=winner.area_id,
            ws_conn=winner.ws,
            ha_client=self.ha_client,
            stt_engine=self.stt_engine,
            tts_engine=self.tts_engine,
            audio_store=self.audio_store,
            on_recording_saved_callback=self._broadcast_new_recording,
            audio_cleaner=self.audio_cleaner
        )
        self.active_sessions[winner.ws] = session

    async def _broadcast_new_recording(self, recording_entry: Dict[str, Any]):
        """Broadcast new recording event to connected Web UI clients."""
        payload = json.dumps({"event": "new_recording", "recording": recording_entry}, ensure_ascii=False)
        disconnected = set()
        for client in self.web_ui_clients:
            try:
                await client.send_str(payload)
            except Exception:
                disconnected.add(client)
        self.web_ui_clients.difference_update(disconnected)

    async def _broadcast_debug_audio(self, device_id: str, raw_pcm: bytes, cleaned_pcm: bytes):
        """Broadcast real-time debug audio PCM stream (RAW & Cleaned) to Web UI clients."""
        if not self.web_ui_clients:
            return
        payload = json.dumps({
            "event": "debug_audio",
            "device_id": device_id,
            "raw_pcm": base64.b64encode(raw_pcm).decode("ascii"),
            "cleaned_pcm": base64.b64encode(cleaned_pcm).decode("ascii")
        })
        disconnected = set()
        for client in self.web_ui_clients:
            try:
                await client.send_str(payload)
            except Exception:
                disconnected.add(client)
        self.web_ui_clients.difference_update(disconnected)

    # -------------------------------------------------------------------------
    # HTTP & REST Routes for Web UI
    # -------------------------------------------------------------------------
    async def handle_root(self, request: web.Request) -> web.StreamResponse:
        upgrade_hdr = request.headers.get("Upgrade", "").lower()
        if "websocket" in upgrade_hdr:
            return await self.handle_satellite_ws(request)
        index_path = os.path.join(WEB_DIR, "index.html")
        return web.FileResponse(index_path)

    async def handle_get_recordings(self, request: web.Request) -> web.Response:
        recordings = self.audio_store.get_all_recordings()
        return web.json_response(recordings)

    async def handle_get_satellites(self, request: web.Request) -> web.Response:
        """Return list of connected satellites and their hardware config/debug state."""
        sats = []
        for dev_id, info in self.connected_satellites.items():
            sats.append({
                "device_id": info["device_id"],
                "area_id": info.get("area_id", "default"),
                "debug_mode": info.get("debug_mode", False),
                "mic_gain": info.get("mic_gain", 1.0),
                "speaker_volume": info.get("speaker_volume", 1.0),
                "led_brightness": info.get("led_brightness", 255),
                "vad_multiplier": info.get("vad_multiplier", 3.5),
                "dc_removal_enabled": info.get("dc_removal_enabled", True),
                "noise_tracking_enabled": info.get("noise_tracking_enabled", True),
                "mic_gain_enabled": info.get("mic_gain_enabled", True),
                "speaker_volume_enabled": info.get("speaker_volume_enabled", True),
                "wakenet_threshold": info.get("wakenet_threshold", 0),
                "mic_shift": info.get("mic_shift", 14),
                "stream_max_ms": info.get("stream_max_ms", 10000),
                "stream_silence_end_ms": info.get("stream_silence_end_ms", 1500),
                "mic_slot": info.get("mic_slot", -1)
            })
        return web.json_response(sats)

    async def handle_update_satellite_config(self, request: web.Request) -> web.Response:
        """Send remote configuration update to target satellite and persist in NVS on device."""
        try:
            data = await request.json()
            device_id = data.get("device_id")
            new_config = data.get("config", {})
            sat_info = self.connected_satellites.get(device_id)
            if not sat_info:
                return web.json_response({"status": "error", "message": f"Satellite '{device_id}' not connected"}, status=404)

            # Update server state memory
            for k in ["area_id", "mic_gain", "speaker_volume", "led_brightness", "vad_multiplier",
                      "dc_removal_enabled", "noise_tracking_enabled", "mic_gain_enabled",
                      "speaker_volume_enabled", "wakenet_threshold", "mic_shift", "stream_max_ms",
                      "stream_silence_end_ms", "mic_slot"]:
                if k in new_config:
                    sat_info[k] = new_config[k]

            # Send update_config JSON payload over WS to satellite
            msg_json = ConfigUpdateMessage(config=new_config).to_json()
            await sat_info["ws"].send(msg_json)
            logger.info(f"Sent 'update_config' payload to satellite '{device_id}': {new_config}")
            return web.json_response({"status": "ok", "device_id": device_id, "config": new_config})
        except Exception as e:
            logger.error(f"Error updating satellite config: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    async def handle_start_debug_stream(self, request: web.Request) -> web.Response:
        """Send signal to target satellite to start continuous audio debug streaming."""
        try:
            data = await request.json()
            device_id = data.get("device_id")
            sat_info = self.connected_satellites.get(device_id)
            if not sat_info:
                return web.json_response({"status": "error", "message": f"Satellite '{device_id}' not connected"}, status=404)
            
            sat_info["debug_mode"] = True
            control_frame = ControlMessage(action="start_debug", reason="web_ui_debug_enabled").to_json()
            await sat_info["ws"].send(control_frame)
            logger.info(f"Sent 'start_debug' signal to satellite '{device_id}'")
            return web.json_response({"status": "ok", "device_id": device_id, "debug_mode": True})
        except Exception as e:
            logger.error(f"Error starting debug stream: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    async def handle_stop_debug_stream(self, request: web.Request) -> web.Response:
        """Send signal to target satellite to stop continuous audio debug streaming."""
        try:
            data = await request.json()
            device_id = data.get("device_id")
            sat_info = self.connected_satellites.get(device_id)
            if sat_info:
                sat_info["debug_mode"] = False
                control_frame = ControlMessage(action="stop_debug", reason="web_ui_debug_disabled").to_json()
                await sat_info["ws"].send(control_frame)
                logger.info(f"Sent 'stop_debug' signal to satellite '{device_id}'")
            return web.json_response({"status": "ok", "device_id": device_id, "debug_mode": False})
        except Exception as e:
            logger.error(f"Error stopping debug stream: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    async def handle_get_audio_settings(self, request: web.Request) -> web.Response:
        """Return current DSP audio processing, VAD, and satellite arbitration settings."""
        settings = self.audio_cleaner.get_settings()
        settings["arbiter_window_ms"] = self.config.arbiter_window_ms
        settings["arbiter_cooldown_sec"] = self.config.arbiter_cooldown_sec
        settings["vad_silence_ms"] = self.config.vad_silence_ms
        settings["vad_threshold"] = self.config.vad_threshold
        return web.json_response(settings)

    async def handle_update_audio_settings(self, request: web.Request) -> web.Response:
        """Update DSP noise suppression, gain multiplier, HPF, AGC, VAD, arbiter window, and cooldown settings dynamically."""
        try:
            data = await request.json()
            updated = self.audio_cleaner.update_settings(
                denoise_enabled=data.get("denoise_enabled"),
                noise_suppression_level=data.get("noise_suppression_level"),
                gain_multiplier=data.get("gain_multiplier"),
                target_speech_rms=data.get("target_speech_rms"),
                hpf_cutoff_hz=data.get("hpf_cutoff_hz"),
                agc_max_gain=data.get("agc_max_gain")
            )
            from server.config import update_dotenv_var

            if "hpf_cutoff_hz" in data and data["hpf_cutoff_hz"] is not None:
                val = float(data["hpf_cutoff_hz"])
                self.config.hpf_cutoff_hz = val
                update_dotenv_var("HPF_CUTOFF_HZ", str(val))
                updated["hpf_cutoff_hz"] = val

            if "agc_max_gain" in data and data["agc_max_gain"] is not None:
                val = float(data["agc_max_gain"])
                self.config.agc_max_gain = val
                update_dotenv_var("AGC_MAX_GAIN", str(val))
                updated["agc_max_gain"] = val

            if "vad_silence_ms" in data and data["vad_silence_ms"] is not None:
                val = int(data["vad_silence_ms"])
                self.config.vad_silence_ms = val
                update_dotenv_var("VAD_SILENCE_MS", str(val))
                updated["vad_silence_ms"] = val

            if "vad_threshold" in data and data["vad_threshold"] is not None:
                val = float(data["vad_threshold"])
                self.config.vad_threshold = val
                update_dotenv_var("VAD_THRESHOLD", str(val))
                updated["vad_threshold"] = val

            if "arbiter_window_ms" in data and data["arbiter_window_ms"] is not None:
                val = float(data["arbiter_window_ms"])
                val = max(50.0, min(3000.0, val))
                self.config.arbiter_window_ms = val
                self.arbiter.window_seconds = val / 1000.0
                update_dotenv_var("ARBITER_WINDOW_MS", str(val))
                updated["arbiter_window_ms"] = val

            if "arbiter_cooldown_sec" in data and data["arbiter_cooldown_sec"] is not None:
                c_val = float(data["arbiter_cooldown_sec"])
                c_val = max(0.0, min(10.0, c_val))
                self.config.arbiter_cooldown_sec = c_val
                update_dotenv_var("ARBITER_COOLDOWN_SEC", str(c_val))
                updated["arbiter_cooldown_sec"] = c_val

            logger.info(f"Updated audio & arbiter settings from Web UI: {updated}")
            return web.json_response({"status": "ok", "settings": updated})
        except Exception as e:
            logger.error(f"Failed to update audio settings: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    async def handle_get_audio_file(self, request: web.Request) -> web.StreamResponse:
        filename = request.match_info.get("filename", "")
        file_path = self.audio_store.get_file_path(filename)
        if not file_path:
            return web.HTTPNotFound(text="Audio recording file not found")
        return web.FileResponse(file_path, headers={"Content-Type": "audio/wav"})

    async def handle_restart_server(self, request: web.Request) -> web.Response:
        """Handle server process restart request from Web UI."""
        logger.info("Restart request received from Web UI.")
        asyncio.create_task(self._schedule_restart())
        return web.json_response({"status": "restarting", "message": "Сервер перезапускается..."})

    async def _schedule_restart(self):
        """Schedule graceful shutdown and process restart."""
        await asyncio.sleep(0.5)
        logger.info("Executing server process restart...")
        try:
            await self.stop()
        except Exception as e:
            logger.warning(f"Error during stop before restart: {e}")

        import sys
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv)
        os._exit(0)

    # -------------------------------------------------------------------------
    # Web UI Browser WebSocket Handler (/ws)
    # -------------------------------------------------------------------------
    async def handle_web_ui_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        logger.info(f"Web UI Browser connected from {request.remote}")
        self.web_ui_clients.add(ws)
        try:
            async for msg in ws:
                pass
        finally:
            self.web_ui_clients.discard(ws)
            logger.info("Web UI Browser disconnected")
        return ws

    # -------------------------------------------------------------------------
    # ESP32-S3 Voice Satellite WebSocket Handler (/ and /ws_satellite)
    # -------------------------------------------------------------------------
    async def handle_satellite_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=10 * 1024 * 1024)
        await ws.prepare(request)

        class AiohttpWsAdapter:
            def __init__(self, ws_obj: web.WebSocketResponse):
                self._ws = ws_obj

            async def send(self, data: Any):
                if isinstance(data, (bytes, bytearray)):
                    await self._ws.send_bytes(data)
                elif isinstance(data, str):
                    await self._ws.send_str(data)

        ws_adapter = AiohttpWsAdapter(ws)
        logger.info(f"Satellite connected from {request.remote} (handshake 101 OK)")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    parsed = parse_incoming_message(msg.data)
                    if isinstance(parsed, InitMessage):
                        existing_sat = self.connected_satellites.get(parsed.device_id)
                        if existing_sat and existing_sat.get("ws") != ws_adapter:
                            logger.warning(
                                f"⚠️ DUPLICATE DEVICE ID DETECTED: Satellite at {request.remote} is using device_id='{parsed.device_id}', "
                                f"which is ALREADY connected from another socket! Ensure each satellite has a unique Device ID in settings."
                            )
                        logger.info(f"Received INIT from device '{parsed.device_id}' (area: '{parsed.area_id}', rms: {parsed.rms:.2f})")
                        self.connected_satellites[parsed.device_id] = {
                            "device_id": parsed.device_id,
                            "area_id": parsed.area_id,
                            "ws": ws_adapter,
                            "debug_mode": self.connected_satellites.get(parsed.device_id, {}).get("debug_mode", False),
                            "mic_gain": parsed.mic_gain,
                            "speaker_volume": parsed.speaker_volume,
                            "led_brightness": parsed.led_brightness,
                            "vad_multiplier": parsed.vad_multiplier,
                            "dc_removal_enabled": parsed.dc_removal_enabled,
                            "noise_tracking_enabled": parsed.noise_tracking_enabled,
                            "mic_gain_enabled": parsed.mic_gain_enabled,
                            "speaker_volume_enabled": parsed.speaker_volume_enabled,
                            "wakenet_threshold": parsed.wakenet_threshold,
                            "mic_shift": parsed.mic_shift,
                            "stream_max_ms": parsed.stream_max_ms,
                            "stream_silence_end_ms": parsed.stream_silence_end_ms,
                            "mic_slot": parsed.mic_slot
                        }
                        self.ws_to_device_id[ws_adapter] = parsed.device_id
                        if parsed.rms > 0:
                            await self.arbiter.register_candidate(parsed, ws_adapter)

                elif msg.type == WSMsgType.BINARY:
                    # PCM binary audio payload
                    device_id = self.ws_to_device_id.get(ws_adapter)
                    sat_info = self.connected_satellites.get(device_id) if device_id else None

                    if sat_info and sat_info.get("debug_mode"):
                        raw_pcm = msg.data
                        cleaned_pcm = self.audio_cleaner.process_pcm16(raw_pcm)
                        await self._broadcast_debug_audio(device_id, raw_pcm, cleaned_pcm)

                    session = self.active_sessions.get(ws_adapter)
                    if session:
                        finished = await session.handle_audio_chunk(msg.data)
                        if finished:
                            logger.info(f"Finished pipeline for session '{session.device_id}'")
                            import time
                            self.last_session_end_time = time.time()
                            if ws_adapter in self.active_sessions:
                                del self.active_sessions[ws_adapter]

        except Exception as e:
            logger.error(f"Satellite connection error: {e}")
        finally:
            logger.info("Satellite client disconnected")
            if ws_adapter in self.active_sessions:
                del self.active_sessions[ws_adapter]
            device_id = self.ws_to_device_id.pop(ws_adapter, None)
            if device_id and device_id in self.connected_satellites:
                del self.connected_satellites[device_id]

        return ws

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/recordings", self.handle_get_recordings)
        app.router.add_get("/recordings/{filename}", self.handle_get_audio_file)
        app.router.add_get("/api/satellites", self.handle_get_satellites)
        app.router.add_post("/api/satellite/config", self.handle_update_satellite_config)
        app.router.add_post("/api/debug_stream/start", self.handle_start_debug_stream)
        app.router.add_post("/api/debug_stream/stop", self.handle_stop_debug_stream)
        app.router.add_get("/api/audio_settings", self.handle_get_audio_settings)
        app.router.add_post("/api/audio_settings", self.handle_update_audio_settings)
        app.router.add_post("/api/restart", self.handle_restart_server)
        app.router.add_get("/ws", self.handle_web_ui_ws)
        app.router.add_get("/ws_satellite", self.handle_satellite_ws)
        app.router.add_get("/", self.handle_root)
        return app

    async def start(self):
        await self.ha_client.connect()
        app = self.create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info("==========================================================")
        logger.info(f"🚀 Central Voice Server & Web UI active on http://localhost:{self.port}")
        logger.info("==========================================================")
        return AppRunnerAdapter(runner, self.ha_client)

    async def stop(self):
        """Cleanly shutdown Home Assistant connection and server."""
        await self.ha_client.close()


async def main():
    server_app = CentralVoiceServer()
    try:
        await server_app.start()
        await asyncio.Event().wait()
    finally:
        await server_app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopping Voice Server...")
