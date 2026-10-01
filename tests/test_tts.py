"""
Unit tests for Piper TTS engine and streaming audio response.
"""
import pytest
import asyncio
from server.domain.audio import PiperTTSEngine



@pytest.mark.asyncio
async def test_piper_tts_synthesis_stream():
    tts = PiperTTSEngine()
    text = "Свет включен"

    chunks = []
    async for chunk in tts.synthesize_stream(text, chunk_size=512):
        assert isinstance(chunk, bytes)
        assert len(chunk) <= 512
        chunks.append(chunk)

    total_bytes = sum(len(c) for c in chunks)
    assert total_bytes > 0
    # 16kHz 16-bit mono = 32,000 bytes per second
    # "Свет включен" should generate around 0.96s audio (~30,720 bytes)
    assert total_bytes >= 10000
