"""
Unit tests for VAD silence detector and STT streaming interface.
"""
import pytest
import numpy as np
from server.domain.audio import SileroVADDetector, SherpaSTTEngine



def test_vad_speech_and_silence_detection():
    # 16kHz 16-bit Mono -> 32,000 bytes per second
    vad = SileroVADDetector(sample_rate=16000, silence_duration_ms=500, threshold=0.5)

    # 1. Generate 300 ms of active speech audio (sine wave high amplitude)
    t = np.linspace(0, 0.3, int(16000 * 0.3))
    speech_int16 = (np.sin(2 * np.pi * 440 * t) * 15000).astype(np.int16)
    speech_bytes = speech_int16.tobytes()

    is_end, prob = vad.process_chunk(speech_bytes)
    assert not is_end
    assert prob >= 0.5
    assert vad.is_speech_active is True

    # 2. Generate 600 ms of silence (zeros)
    silence_int16 = np.zeros(int(16000 * 0.6), dtype=np.int16)
    silence_bytes = silence_int16.tobytes()

    is_end, prob = vad.process_chunk(silence_bytes)
    assert is_end is True  # Trailing silence > 500ms should trigger end of speech


def test_stt_stream_transcription():
    stt = SherpaSTTEngine()
    stream = stt.create_stream()

    # Pass dummy audio frame
    audio_int16 = np.zeros(1600, dtype=np.int16)
    stream.accept_waveform(audio_int16.tobytes())

    stream.set_mock_transcript("Включи свет в спальне")
    result = stream.get_result()
    assert result == "Включи свет в спальне"
