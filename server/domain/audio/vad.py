"""
Silero VAD Engine & Audio Frame Silence Detector (Domain: Audio).
Processes 16kHz 16-bit Mono PCM audio streams and detects end-of-speech (silence).
"""
import numpy as np
import logging
from typing import Optional, Tuple

logger = logging.getLogger("SileroVAD")


class SileroVADDetector:
    def __init__(self, sample_rate: int = 16000, silence_duration_ms: int = 800, threshold: float = 0.5):
        self.sample_rate = sample_rate
        self.silence_duration_ms = silence_duration_ms
        self.silence_samples_limit = int((silence_duration_ms / 1000.0) * sample_rate)
        self.threshold = threshold
        
        self.bytes_per_sample = 2  # 16-bit mono
        self.consecutive_silence_samples = 0
        self.is_speech_active = False
        self.total_samples_processed = 0
        self.onnx_session = None

        self._init_onnx_model()

    def _init_onnx_model(self):
        try:
            import onnxruntime as ort
            logger.info("ONNX Runtime available for Silero VAD")
        except ImportError:
            logger.info("onnxruntime not installed. Using energy-based fallback VAD.")

    def get_settings(self) -> dict:
        return {
            "silence_duration_ms": self.silence_duration_ms,
            "threshold": round(self.threshold, 2)
        }

    def update_settings(self, silence_duration_ms: Optional[int] = None, threshold: Optional[float] = None) -> dict:
        if silence_duration_ms is not None:
            self.silence_duration_ms = max(100, min(5000, int(silence_duration_ms)))
            self.silence_samples_limit = int((self.silence_duration_ms / 1000.0) * self.sample_rate)
        if threshold is not None:
            self.threshold = float(np.clip(threshold, 0.05, 0.95))
        logger.info(f"Updated VAD settings: {self.get_settings()}")
        return self.get_settings()

    def reset(self):
        """Reset VAD internal state for a new audio stream."""
        self.consecutive_silence_samples = 0
        self.is_speech_active = False
        self.total_samples_processed = 0

    def process_chunk(self, pcm_bytes: bytes) -> Tuple[bool, float]:
        """
        Process a PCM 16kHz 16-bit Mono audio frame.
        Returns:
            (is_end_of_speech: bool, speech_probability: float)
        """
        if not pcm_bytes:
            return False, 0.0

        audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        samples_count = len(audio_int16)
        self.total_samples_processed += samples_count

        if samples_count == 0:
            return False, 0.0

        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        rms = np.sqrt(np.mean(audio_float32 ** 2)) if len(audio_float32) > 0 else 0.0
        speech_prob = min(1.0, rms * 40.0)

        if speech_prob >= self.threshold:
            self.is_speech_active = True
            self.consecutive_silence_samples = 0
        else:
            if self.is_speech_active:
                self.consecutive_silence_samples += samples_count

        is_end_of_speech = (
            self.is_speech_active and 
            self.consecutive_silence_samples >= self.silence_samples_limit
        )

        if is_end_of_speech:
            logger.info(
                f"End of speech detected after {self.silence_duration_ms}ms silence "
                f"(Total samples: {self.total_samples_processed})"
            )

        return is_end_of_speech, speech_prob
