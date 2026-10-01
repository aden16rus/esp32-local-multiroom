"""
Unit tests for Protocol parsing and Multi-Room Arbiter module.
"""
import pytest
import asyncio
import json
from server.domain.satellite import InitMessage, ControlMessage, parse_incoming_message, MultiRoomArbiter, ArbiterCandidate



class MockWebSocket:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.sent_messages = []

    async def send(self, message: str):
        self.sent_messages.append(json.loads(message))


def test_protocol_init_parse():
    data = '{"event": "init", "device_id": "esp_living", "area_id": "living_room", "rms": 1540.5}'
    msg = parse_incoming_message(data)
    assert isinstance(msg, InitMessage)
    assert msg.device_id == "esp_living"
    assert msg.area_id == "living_room"
    assert msg.rms == 1540.5


def test_protocol_invalid():
    with pytest.raises(ValueError):
        parse_incoming_message('{"event": "init", "device_id": "esp1"}')


@pytest.mark.asyncio
async def test_arbiter_highest_rms_wins():
    arbiter = MultiRoomArbiter(window_ms=100.0)

    ws_living = MockWebSocket("esp_living")
    ws_bedroom = MockWebSocket("esp_bedroom")
    ws_kitchen = MockWebSocket("esp_kitchen")

    init_living = InitMessage(device_id="esp_living", area_id="living_room", rms=2500.0)
    init_bedroom = InitMessage(device_id="esp_bedroom", area_id="bedroom", rms=4200.0)  # Highest RMS
    init_kitchen = InitMessage(device_id="esp_kitchen", area_id="kitchen", rms=1200.0)

    winner_result = []

    async def on_winner(winner: ArbiterCandidate):
        winner_result.append(winner)

    # Register all 3 candidates nearly simultaneously
    await asyncio.gather(
        arbiter.register_candidate(init_living, ws_living, on_winner),
        arbiter.register_candidate(init_bedroom, ws_bedroom, on_winner),
        arbiter.register_candidate(init_kitchen, ws_kitchen, on_winner),
    )

    # Wait slightly longer than the 100ms window
    await asyncio.sleep(0.15)

    assert len(winner_result) == 1
    assert winner_result[0].device_id == "esp_bedroom"
    assert winner_result[0].area_id == "bedroom"

    # Verify control responses sent to sockets
    assert len(ws_bedroom.sent_messages) == 1
    assert ws_bedroom.sent_messages[0]["action"] == "continue"

    assert len(ws_living.sent_messages) == 1
    assert ws_living.sent_messages[0]["action"] == "stop"

    assert len(ws_kitchen.sent_messages) == 1
    assert ws_kitchen.sent_messages[0]["action"] == "stop"


@pytest.mark.asyncio
async def test_arbiter_duplicate_device_ids_only_winning_ws_continues():
    """Ensure that if two satellites have the same device_id string, only the higher RMS socket gets 'continue'."""
    arbiter = MultiRoomArbiter(window_ms=50.0)

    ws_sat1 = MockWebSocket("esp32_satellite")
    ws_sat2 = MockWebSocket("esp32_satellite")

    init_sat1 = InitMessage(device_id="esp32_satellite", area_id="room1", rms=3000.0)  # Higher RMS
    init_sat2 = InitMessage(device_id="esp32_satellite", area_id="room1", rms=1500.0)

    await asyncio.gather(
        arbiter.register_candidate(init_sat1, ws_sat1),
        arbiter.register_candidate(init_sat2, ws_sat2),
    )
    await asyncio.sleep(0.08)

    assert len(ws_sat1.sent_messages) == 1
    assert ws_sat1.sent_messages[0]["action"] == "continue"

    assert len(ws_sat2.sent_messages) == 1
    assert ws_sat2.sent_messages[0]["action"] == "stop"


@pytest.mark.asyncio
async def test_arbiter_rejects_candidate_when_session_active():
    """Ensure that candidates are immediately rejected with 'stop' if a session is currently active."""
    is_active = True
    arbiter = MultiRoomArbiter(window_ms=50.0, is_session_active_fn=lambda: is_active)

    ws_new = MockWebSocket("esp_late")
    init_late = InitMessage(device_id="esp_late", area_id="hall", rms=4000.0)

    await arbiter.register_candidate(init_late, ws_new)

    # Rejection happens immediately without waiting for window
    assert len(ws_new.sent_messages) == 1
    assert ws_new.sent_messages[0]["action"] == "stop"
    assert ws_new.sent_messages[0]["reason"] == "system_busy_active_session"

