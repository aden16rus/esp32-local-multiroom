"""
Unit & Integration tests for Home Assistant WS API client.
"""
import pytest
import asyncio
import json
import websockets
from server.domain.homeassistant import HomeAssistantClient



@pytest.mark.asyncio
async def test_ha_client_fallback_mode():
    client = HomeAssistantClient(url="ws://127.0.0.1:9999/api/websocket")
    await client.connect()  # Should gracefully set offline when server unreachable
    
    response = await client.process_conversation("Включи свет", device_id="esp_bedroom", area_id="bedroom")
    assert response == "Устройство не найдено"

    await client.close()


@pytest.mark.asyncio
async def test_ha_client_direct_action_execution():
    sent_actions = []

    client = HomeAssistantClient(url="ws://127.0.0.1:18123/api/websocket", token="TEST_TOKEN")

    async def mock_get_all_states():
        return [
            {
                "entity_id": "light.kitchen_light",
                "state": "off",
                "attributes": {"friendly_name": "Свет на кухне"}
            }
        ]
    client.rest.get_all_states = mock_get_all_states

    async def mock_send_action(domain, action, entity_id, extra_data=None):
        sent_actions.append((domain, action, entity_id))
        return {"status": "ok"}
    client.rest.send_action = mock_send_action

    text_resp = await client.process_conversation("Включи свет на кухне", device_id="esp_kitchen", area_id="kitchen")

    assert len(sent_actions) == 1
    assert sent_actions[0] == ("homeassistant", "turn_on", "light.kitchen_light")
    assert "включен" in text_resp.lower() or "свет" in text_resp.lower()

    await client.close()
