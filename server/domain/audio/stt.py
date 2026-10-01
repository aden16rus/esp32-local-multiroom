"""
Sherpa-ONNX STT Engine Integration (Domain: Audio).
Supports high-accuracy Russian models for CPU execution:
- Sber GigaAM v2 Transducer (SOTA accuracy for Russian speech, ~225MB int8)
- Zipformer RU int8 / standard models (~67MB)
- Zipformer small models (~26MB)
"""
import os
import glob
import numpy as np
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("SherpaSTT")

DEFAULT_MODEL_DIRS = [
    # 1. Sber GigaAM v2 Transducer (SOTA accuracy for Russian voice commands, ~225MB int8)
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "models", "sherpa-onnx-nemo-transducer-giga-am-v2-russian-2025-04-19")),
    # 2. Medium Zipformer RU int8 (~67MB)
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "models", "sherpa-onnx-zipformer-ru-int8-2025-04-20")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "models", "sherpa-onnx-zipformer-ru-2025-04-20")),
    # 3. Small Zipformer RU models
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "models", "sherpa-onnx-streaming-zipformer-small-ru-vosk-2025-08-16")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "models", "sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16")),
]


class SherpaSTTEngine:
    def __init__(self, model_path: Optional[str] = None):
        env_model_path = os.getenv("STT_MODEL_PATH") or os.getenv("STT_MODEL_DIR")
        self.model_path = model_path or env_model_path
        self.recognizer = None
        self.is_offline = False
        self.active_model_name = ""
        self._init_engine()

    def _find_file(self, target_dir: str, patterns: List[str]) -> Optional[str]:
        for pattern in patterns:
            matches = glob.glob(os.path.join(target_dir, pattern))
            if matches:
                int8_matches = [m for m in matches if "int8" in os.path.basename(m).lower()]
                if int8_matches:
                    return int8_matches[0]
                return matches[0]
        return None

    def _init_engine(self):
        try:
            import sherpa_onnx

            target_dirs = []
            if self.model_path and os.path.exists(self.model_path):
                target_dirs.append(self.model_path)
            target_dirs.extend([d for d in DEFAULT_MODEL_DIRS if os.path.exists(d)])

            if not target_dirs:
                logger.info("No local ONNX model directory found. STT interface active.")
                return

            for target_dir in target_dirs:
                tokens = os.path.join(target_dir, "tokens.txt")
                if not os.path.exists(tokens):
                    continue

                encoder = self._find_file(target_dir, ["*encoder*.onnx", "encoder*.onnx"])
                decoder = self._find_file(target_dir, ["*decoder*.onnx", "decoder*.onnx"])
                joiner = self._find_file(target_dir, ["*joiner*.onnx", "joiner*.onnx"])

                if not (encoder and decoder and joiner):
                    logger.warning(f"Incomplete transducer files in {target_dir}")
                    continue

                dir_name = os.path.basename(target_dir).lower()

                # 1. Sber GigaAM v2 Transducer (Offline NeMo Transducer)
                if "giga-am" in dir_name or "giga_am" in dir_name:
                    try:
                        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                            tokens=tokens,
                            encoder=encoder,
                            decoder=decoder,
                            joiner=joiner,
                            num_threads=4,
                            sample_rate=16000,
                            feature_dim=64,
                            model_type="nemo_transducer"
                        )
                        self.is_offline = True
                        self.active_model_name = os.path.basename(target_dir)
                        logger.info(f"Loaded Sber GigaAM v2 Transducer (SOTA Russian STT) from: {target_dir}")
                        return
                    except Exception as err:
                        logger.warning(f"Failed to load GigaAM model from {target_dir}: {err}")

                # 2. Offline Transducer (e.g. sherpa-onnx-zipformer-ru-int8-2025-04-20)
                try:
                    self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                        tokens=tokens,
                        encoder=encoder,
                        decoder=decoder,
                        joiner=joiner,
                        num_threads=4,
                        sample_rate=16000,
                        feature_dim=80,
                    )
                    self.is_offline = True
                    self.active_model_name = os.path.basename(target_dir)
                    logger.info(f"Loaded Offline Zipformer STT model from: {target_dir}")
                    return
                except Exception:
                    pass

                # 3. Online Transducer (e.g. streaming zipformer small)
                try:
                    self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                        tokens=tokens,
                        encoder=encoder,
                        decoder=decoder,
                        joiner=joiner,
                        num_threads=2,
                        sample_rate=16000,
                        feature_dim=80,
                        enable_endpoint_detection=True,
                    )
                    self.is_offline = False
                    self.active_model_name = os.path.basename(target_dir)
                    logger.info(f"Loaded Streaming Zipformer STT model from: {target_dir}")
                    return
                except Exception as err:
                    logger.warning(f"Failed to load Online Transducer from {target_dir}: {err}")

            logger.warning("Could not initialize any Sherpa-ONNX model. Fallback interface active.")
        except Exception as e:
            logger.warning(f"Failed to initialize sherpa_onnx recognizer: {e}. Fallback interface active.")

    def create_stream(self) -> "STTStream":
        return STTStream(self)


class STTStream:
    def __init__(self, engine: SherpaSTTEngine):
        self.engine = engine
        self._pcm_buffer = bytearray()
        self._float_samples = []
        self._simulated_text: Optional[str] = None
        self.sherpa_stream = None

        if self.engine.recognizer is not None:
            self.sherpa_stream = self.engine.recognizer.create_stream()

    def accept_waveform(self, pcm_bytes: bytes):
        """Accept raw 16kHz 16-bit mono PCM bytes."""
        if not pcm_bytes:
            return
        self._pcm_buffer.extend(pcm_bytes)

        samples_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        samples_float32 = samples_int16.astype(np.float32) / 32768.0

        if self.engine.is_offline:
            self._float_samples.extend(samples_float32.tolist())
        elif self.sherpa_stream is not None:
            self.sherpa_stream.accept_waveform(16000, samples_float32)
            while self.engine.recognizer.is_ready(self.sherpa_stream):
                self.engine.recognizer.decode_stream(self.sherpa_stream)

    def set_mock_transcript(self, text: str):
        """For testing & simulation without model binaries."""
        self._simulated_text = text

    def get_result(self) -> str:
        """Finalize recognition and return transcribed text."""
        if self._simulated_text:
            return self._simulated_text

        if self.engine.recognizer is None:
            return ""

        if self.engine.is_offline:
            if not self._float_samples:
                return ""
            stream = self.engine.recognizer.create_stream()
            samples_np = np.array(self._float_samples, dtype=np.float32)
            stream.accept_waveform(16000, samples_np)
            self.engine.recognizer.decode_stream(stream)
            res = stream.result.text.strip()
            return res
        elif self.sherpa_stream is not None:
            result = self.engine.recognizer.get_result(self.sherpa_stream)
            text = result.text.strip() if hasattr(result, "text") else str(result).strip()
            return text

        return ""

