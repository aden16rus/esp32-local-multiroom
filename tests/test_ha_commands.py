"""
Integration & Unit tests for Home Assistant API text command processing.
Tests text commands 'включи свет на кухне' and 'выключи свет на кухне' via WebSocket Conversation API
and REST service calls.
"""
import pytest
import asyncio
import json
import websockets
from server.domain.homeassistant import HomeAssistantClient, HAConfig, HARestClient


@pytest.mark.asyncio
async def test_ha_conversation_light_commands():
    """
    Test processing text commands 'включи свет на кухне' and 'выключи свет на кухне'
    via direct HA REST service action calls.
    """
    client = HomeAssistantClient(url="ws://127.0.0.1:18124/api/websocket", token="MOCK_TOKEN")

    async def mock_get_all_states():
        return [
            {
                "entity_id": "light.kitchen_light",
                "state": "off",
                "attributes": {"friendly_name": "Свет на кухне"}
            }
        ]
    client.rest.get_all_states = mock_get_all_states

    sent_actions = []
    async def mock_send_action(domain, action, entity_id, extra_data=None):
        sent_actions.append((domain, action, entity_id))
        return {"status": "ok"}
    client.rest.send_action = mock_send_action

    # Test command 1: "включи свет на кухне"
    resp_on = await client.process_conversation("включи свет на кухне", device_id="sat_kitchen", area_id="kitchen")
    assert resp_on == "Свет на кухне включен"

    # Test command 2: "выключи свет на кухне"
    resp_off = await client.process_conversation("выключи свет на кухне", device_id="sat_kitchen", area_id="kitchen")
    assert resp_off == "Свет на кухне выключен"

    assert len(sent_actions) == 2
    assert sent_actions[0] == ("homeassistant", "turn_on", "light.kitchen_light")
    assert sent_actions[1] == ("homeassistant", "turn_off", "light.kitchen_light")

    await client.close()


@pytest.mark.asyncio
async def test_ha_fallback_light_commands():
    """
    Test text commands when Home Assistant server is offline and no matching entities exist.
    """
    client = HomeAssistantClient(url="ws://127.0.0.1:9998/api/websocket")
    await client.connect()  # Fails gracefully

    resp_on = await client.process_conversation("включи свет на кухне", device_id="sat_kitchen", area_id="kitchen")
    assert resp_on == "Устройство не найдено"

    await client.close()


def test_fuzzy_entity_matcher_extractor_fan():
    """
    Test FuzzyEntityMatcher matching 'включи вытяжку в туалете' to 'Вытяжка в туалете'.
    """
    from server.domain.homeassistant.matcher import FuzzyEntityMatcher

    mock_entities = [
        {
            "entity_id": "binary_sensor.unknown_button",
            "state": "off",
            "attributes": {"friendly_name": "Вытяжка в туалете Button"}
        },
        {
            "entity_id": "fan.vytyazhka_v_tualete",
            "state": "off",
            "attributes": {"friendly_name": "Вытяжка в туалете"}
        },
        {
            "entity_id": "light.tualet",
            "state": "off",
            "attributes": {"friendly_name": "Свет в туалете"}
        },
        {
            "entity_id": "light.kitchen",
            "state": "off",
            "attributes": {"friendly_name": "Свет на кухне"}
        }
    ]

    # Test "включи вытяжку в туалете"
    match = FuzzyEntityMatcher.match_entity("включи вытяжку в туалете", mock_entities, area_id="toilet")
    assert match is not None
    assert match["entity_id"] == "fan.vytyazhka_v_tualete"
    assert match["action"] == "turn_on"
    assert "Вытяжка в туалете включена" in match["speech_response"]

    # Test "выключи вытяжку в туалете"
    match_off = FuzzyEntityMatcher.match_entity("выключи вытяжку в туалете", mock_entities, area_id="toilet")
    assert match_off is not None
    assert match_off["entity_id"] == "fan.vytyazhka_v_tualete"
    assert match_off["action"] == "turn_off"
    assert "Вытяжка в туалете выключена" in match_off["speech_response"]


def test_fuzzy_entity_matcher_voice_control_filter():
    """
    Test filtering entities using custom attribute voice_control: true / false.
    """
    from server.domain.homeassistant.matcher import FuzzyEntityMatcher

    mock_entities = [
        {
            "entity_id": "fan.extra_exhaust",
            "state": "off",
            "attributes": {"friendly_name": "Вытяжка в туалете", "voice_control": False}
        },
        {
            "entity_id": "fan.real_vytyazhka",
            "state": "off",
            "attributes": {"friendly_name": "Вытяжка в туалете", "voice_control": True}
        }
    ]

    match = FuzzyEntityMatcher.match_entity("включи вытяжку в туалете", mock_entities, area_id="toilet")
    assert match is not None
    assert match["entity_id"] == "fan.real_vytyazhka"


@pytest.mark.asyncio
async def test_ha_fallback_extractor_fan_command():
    """
    Test command 'включи вытяжку в туалете' when offline and no states available.
    """
    client = HomeAssistantClient(url="ws://127.0.0.1:9997/api/websocket")
    await client.connect()

    resp_on = await client.process_conversation("включи вытяжку в туалете", device_id="sat_toilet", area_id="toilet")
    assert resp_on == "Устройство не найдено"

    await client.close()


@pytest.mark.asyncio
async def test_ha_direct_entity_action_execution():
    """
    Test direct entity matching and REST action execution.
    """
    client = HomeAssistantClient(url="ws://127.0.0.1:18125/api/websocket", token="MOCK_TOKEN")

    async def mock_get_all_states():
        return [
            {
                "entity_id": "fan.vytyazhka_v_tualete",
                "state": "off",
                "attributes": {"friendly_name": "Вытяжка в туалете", "voice_control": True}
            }
        ]
    client.rest.get_all_states = mock_get_all_states

    sent_actions = []
    async def mock_send_action(domain, action, entity_id, extra_data=None):
        sent_actions.append((domain, action, entity_id))
        return {"status": "ok"}
    client.rest.send_action = mock_send_action

    resp = await client.process_conversation("включи вытяжку в туалете", device_id="sat_toilet", area_id="toilet")
    assert "Вытяжка в туалете включена" in resp
    assert len(sent_actions) == 1
    assert sent_actions[0] == ("homeassistant", "turn_on", "fan.vytyazhka_v_tualete")

    await client.close()


def test_parse_complex_command():
    """
    Test parsing complex voice commands with distributed subjects/locations into sub-commands.
    """
    from server.domain.homeassistant.matcher import parse_complex_command

    # Test 1: "включи свет над кроватью и над столом"
    sub1 = parse_complex_command("включи свет над кроватью и над столом")
    assert len(sub1) == 2
    assert sub1[0] == ("turn_on", "свет над кроватью")
    assert sub1[1] == ("turn_on", "свет над столом")

    # Test 2: "включи вытяжку на кухне и в туалете"
    sub2 = parse_complex_command("включи вытяжку на кухне и в туалете")
    assert len(sub2) == 2
    assert sub2[0] == ("turn_on", "вытяжку на кухне")
    assert sub2[1] == ("turn_on", "вытяжку в туалете")

    # Test 3: "включи свет на кухне и выключи вытяжку в туалете"
    sub3 = parse_complex_command("включи свет на кухне и выключи вытяжку в туалете")
    assert len(sub3) == 2
    assert sub3[0] == ("turn_on", "свет на кухне")
    assert sub3[1] == ("turn_off", "вытяжку в туалете")


def test_fuzzy_entity_matcher_complex_multi_device():
    """
    Test FuzzyEntityMatcher.match_entities matching multiple devices from a complex command.
    """
    from server.domain.homeassistant.matcher import FuzzyEntityMatcher

    mock_entities = [
        {
            "entity_id": "light.bed_light",
            "state": "off",
            "attributes": {"friendly_name": "Свет над кроватью"}
        },
        {
            "entity_id": "light.table_light",
            "state": "off",
            "attributes": {"friendly_name": "Свет над столом"}
        },
        {
            "entity_id": "fan.kitchen_exhaust",
            "state": "off",
            "attributes": {"friendly_name": "Вытяжка на кухне"}
        },
        {
            "entity_id": "fan.toilet_exhaust",
            "state": "off",
            "attributes": {"friendly_name": "Вытяжка в туалете"}
        }
    ]

    # Test "включи свет над кроватью и над столом"
    matches1 = FuzzyEntityMatcher.match_entities("включи свет над кроватью и над столом", mock_entities)
    assert len(matches1) == 2
    assert matches1[0]["entity_id"] == "light.bed_light"
    assert matches1[1]["entity_id"] == "light.table_light"
    assert "Свет над кроватью и Свет над столом включены" in matches1[0]["combined_speech_response"]

    # Test "включи вытяжку на кухне и в туалете"
    matches2 = FuzzyEntityMatcher.match_entities("включи вытяжку на кухне и в туалете", mock_entities)
    assert len(matches2) == 2
    assert matches2[0]["entity_id"] == "fan.kitchen_exhaust"
    assert matches2[1]["entity_id"] == "fan.toilet_exhaust"
    assert "Вытяжка на кухне и Вытяжка в туалете включены" in matches2[0]["combined_speech_response"]


@pytest.mark.asyncio
async def test_ha_complex_multi_device_execution():
    """
    Test end-to-end execution of a complex multi-device command.
    """
    client = HomeAssistantClient(url="ws://127.0.0.1:18126/api/websocket", token="MOCK_TOKEN")

    async def mock_get_all_states():
        return [
            {
                "entity_id": "light.bed_light",
                "state": "off",
                "attributes": {"friendly_name": "Свет над кроватью"}
            },
            {
                "entity_id": "light.table_light",
                "state": "off",
                "attributes": {"friendly_name": "Свет над столом"}
            }
        ]
    client.rest.get_all_states = mock_get_all_states

    sent_actions = []
    async def mock_send_action(domain, action, entity_id, extra_data=None):
        sent_actions.append((domain, action, entity_id))
        return {"status": "ok"}
    client.rest.send_action = mock_send_action

    resp = await client.process_conversation("включи свет над кроватью и над столом", device_id="sat_bedroom", area_id="bedroom")
    assert "Свет над кроватью и Свет над столом включены" in resp
    assert len(sent_actions) == 2
    assert ("homeassistant", "turn_on", "light.bed_light") in sent_actions
    assert ("homeassistant", "turn_on", "light.table_light") in sent_actions

    await client.close()


