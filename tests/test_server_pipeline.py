"""
End-to-End System Pipeline Integration Test.
"""
import pytest
import asyncio
import time
from server.main import CentralVoiceServer
from tests.satellite_simulator import SimulatedSatellite


@pytest.mark.asyncio
async def test_full_system_pipeline_and_multi_room_collision():
    # 1. Start server on test port 18765
    server_app = CentralVoiceServer(host="127.0.0.1", port=18765)
    server_instance = await server_app.start()

    try:
        # Create 3 satellites
        sat_bedroom = SimulatedSatellite("esp_bedroom", "bedroom", server_url="ws://127.0.0.1:18765")
        sat_living = SimulatedSatellite("esp_living", "living_room", server_url="ws://127.0.0.1:18765")
        sat_kitchen = SimulatedSatellite("esp_kitchen", "kitchen", server_url="ws://127.0.0.1:18765")

        start_time = time.time()

        # Concurrent trigger (Bedroom has highest RMS: 6500.0)
        results = await asyncio.gather(
            sat_bedroom.trigger_wake_word(rms=6500.0, mock_speech=True),
            sat_living.trigger_wake_word(rms=2200.0, mock_speech=False),
            sat_kitchen.trigger_wake_word(rms=1100.0, mock_speech=False)
        )

        elapsed = time.time() - start_time

        # Verify arbitration outcomes
        assert results[0] == "COMPLETED"  # Bedroom winner
        assert results[1] == "STOP"       # Living room rejected
        assert results[2] == "STOP"       # Kitchen rejected

        # Verify playback audio received by winner
        assert len(sat_bedroom.received_audio_bytes) > 0

        # Verify end-to-end latency constraint
        assert elapsed < 6.0

    finally:
        server_instance.close()
        await server_instance.wait_closed()


@pytest.mark.asyncio
async def test_restart_endpoint():
    import aiohttp
    server_app = CentralVoiceServer(host="127.0.0.1", port=18766)
    server_instance = await server_app.start()

    # Prevent actual OS process exit during unit test
    async def mock_schedule_restart():
        pass
    server_app._schedule_restart = mock_schedule_restart

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("http://127.0.0.1:18766/api/restart") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data.get("status") == "restarting"
    finally:
        server_instance.close()
        await server_instance.wait_closed()
