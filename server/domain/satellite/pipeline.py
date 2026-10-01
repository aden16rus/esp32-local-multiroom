"""
Satellite Pipeline Session Orchestrator (Domain: Satellite).
Manages VAD -> STT -> HA -> TTS voice pipeline for active satellite sessions.
"""
import time
import logging
from typing import Any, Optional
from server.domain.satellite.protocol import ControlMessage
from server.domain.audio.vad import SileroVADDetector
from server.domain.audio.stt import SherpaSTTEngine
from server.domain.audio.tts import PiperTTSEngine
from server.domain.audio.cleaner import AudioCleaner
from server.domain.homeassistant.client import HomeAssistantClient

logger = logging.getLogger("Pipeline")


class SatellitePipelineSession:
    def __init__(
        self,
        device_id: str,
        area_id: str,
        ws_conn: Any,
        ha_client: HomeAssistantClient,
        stt_engine: SherpaSTTEngine,
        tts_engine: PiperTTSEngine,
        audio_store: Optional[Any] = None,
        on_recording_saved_callback: Optional[Any] = None,
        audio_cleaner: Optional[AudioCleaner] = None
    ):
        self.device_id = device_id
        self.area_id = area_id
        self.ws = ws_conn
        self.ha_client = ha_client
        self.stt_engine = stt_engine
        self.tts_engine = tts_engine
        self.audio_store = audio_store
        self.on_recording_saved_callback = on_recording_saved_callback
        
        self.vad = SileroVADDetector(sample_rate=16000, silence_duration_ms=600)
        self.stt_stream = stt_engine.create_stream()
        self.cleaner = audio_cleaner or AudioCleaner(sample_rate=16000)
        self.start_time = time.time()
        self.processing = False
        self._audio_buffer = bytearray()

    async def handle_audio_chunk(self, pcm_bytes: bytes) -> bool:
        """Process incoming audio chunk. Returns True if pipeline finished."""
        if self.processing:
            return False

        self._audio_buffer.extend(pcm_bytes)
        self.stt_stream.accept_waveform(pcm_bytes)

        is_end_of_speech, prob = self.vad.process_chunk(pcm_bytes)
        duration = time.time() - self.start_time
        
        if is_end_of_speech or duration > 7.0:
            self.processing = True
            await self._execute_pipeline()
            return True

        return False

    async def _execute_pipeline(self):
        """Execute STT -> HA -> TTS pipeline with DSP Audio Cleaning."""
        start_ts = time.time()
        logger.info(f"Executing pipeline for device '{self.device_id}' (area: '{self.area_id}')")
        response_text = ""

        try:
            raw_pcm = bytes(self._audio_buffer)
            cleaned_pcm = self.cleaner.process_pcm16(raw_pcm)

            # Decode cleaned PCM buffer for optimal STT accuracy
            clean_stream = self.stt_engine.create_stream()
            clean_stream.accept_waveform(cleaned_pcm if len(cleaned_pcm) > 0 else raw_pcm)
            transcribed_text = clean_stream.get_result()

            if not transcribed_text.strip():
                transcribed_text = self.stt_stream.get_result()

            logger.info("=" * 60)
            logger.info(f"🎤 [RECOGNIZED COMMAND] Device: '{self.device_id}' (Area: '{self.area_id}'):")
            logger.info(f"   => \"{transcribed_text}\"")
            logger.info("=" * 60)

            if transcribed_text.strip():
                response_text = await self.ha_client.process_conversation(
                    text=transcribed_text,
                    device_id=self.device_id,
                    area_id=self.area_id
                )
            else:
                logger.warning("Empty transcript received from STT engine. Using fallback prompt.")
                response_text = f"Слушаю вас в помещении {self.area_id}"

            logger.info(f"🔊 [RESPONSE / TTS]: \"{response_text}\"")

            async for pcm_chunk in self.tts_engine.synthesize_stream(response_text):
                await self.ws.send(pcm_chunk)

            if self.audio_store and len(cleaned_pcm) > 0:
                rec_entry = self.audio_store.save_recording(
                    pcm_bytes=cleaned_pcm,
                    device_id=self.device_id,
                    area_id=self.area_id,
                    transcribed_text=transcribed_text,
                    ha_response=response_text
                )
                if self.on_recording_saved_callback:
                    try:
                        await self.on_recording_saved_callback(rec_entry)
                    except Exception as err:
                        logger.error(f"Error in on_recording_saved_callback: {err}")

        except Exception as e:
            logger.error(f"Error during pipeline execution: {e}", exc_info=True)
        finally:
            try:
                control_frame = ControlMessage(action="stop", reason="pipeline_completed").to_json()
                await self.ws.send(control_frame)
                logger.info(f"Sent 'stop' control message to device '{self.device_id}' -> Satellite returning to IDLE (Green LED)")
            except Exception as e:
                logger.error(f"Failed to send 'stop' control message to device '{self.device_id}': {e}")

            elapsed = (time.time() - start_ts) * 1000.0
            logger.info(f"Pipeline completed in {elapsed:.1f}ms for device '{self.device_id}'")
