"""
Sherpa-ONNX STT Engine Integration (Domain: Audio).
Alias module for backward compatibility.
"""
from server.domain.audio.stt import SherpaSTTEngine, STTStream, DEFAULT_MODEL_DIRS

__all__ = ["SherpaSTTEngine", "STTStream", "DEFAULT_MODEL_DIRS"]

