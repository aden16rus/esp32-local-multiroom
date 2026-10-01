"""
Home Assistant WebSocket & Conversation API Client Component (Domain: HomeAssistant).
Handles WebSocket connection lifecycle, authentication, and context-aware conversation commands.
"""
import json
import asyncio
import logging
from typing import Dict, Any, Optional
from server.domain.homeassistant.config import HAConfig
from server.domain.homeassistant.matcher import FuzzyEntityMatcher, parse_complex_command

logger = logging.getLogger("HAWebSocketClient")


class HAWebSocketClient:
    """Manages Home Assistant WebSocket API connection and Conversation processing."""
    def __init__(self, config: HAConfig):
        self.config = config
        self._msg_id = 1
        self._ws = None
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Establish WebSocket connection and authenticate."""
        import websockets
        async with self._connect_lock:
            if self._ws is not None:
                try:
                    await asyncio.wait_for(self._ws.ping(), timeout=2.0)
                    return True
                except Exception:
                    self._ws = None

            try:
                logger.info(f"Connecting to Home Assistant WS at {self.config.ws_url}...")
                self._ws = await asyncio.wait_for(websockets.connect(self.config.ws_url), timeout=self.config.timeout)
                
                # Expect auth_required
                msg = await asyncio.wait_for(self._ws.recv(), timeout=self.config.timeout)
                data = json.loads(msg)
                if data.get("type") == "auth_required":
                    auth_payload = {"type": "auth", "access_token": self.config.token}
                    await self._ws.send(json.dumps(auth_payload))
                    auth_resp = await asyncio.wait_for(self._ws.recv(), timeout=self.config.timeout)
                    auth_data = json.loads(auth_resp)
                    if auth_data.get("type") != "auth_ok":
                        raise ConnectionError(f"HA Auth failed: {auth_data}")
                        
                logger.info("Connected and authenticated with Home Assistant WS API")
                return True
            except Exception as e:
                logger.warning(f"Could not connect to Home Assistant at {self.config.ws_url}: {e}. Fallback mode active.")
                self._ws = None
                return False

    async def ensure_connected(self) -> bool:
        if self._ws is None:
            return await self.connect()
        return True

    async def process_conversation(self, text: str, device_id: str, area_id: str, rest_client: Optional[Any] = None) -> str:
        """
        Process natural language command by matching HA entities and executing REST service actions directly.
        Handles single and complex multi-device commands sequentially.
        """
        logger.info(f"Processing voice command: '{text}' from area '{area_id}' (device '{device_id}')")

        if rest_client is None:
            logger.warning("REST client is unavailable for command execution.")
            return "Устройство не найдено"

        try:
            entities = await rest_client.get_all_states()
            if not entities:
                logger.warning("Could not fetch HA entity states via REST API.")
                return "Устройство не найдено"

            matches = FuzzyEntityMatcher.match_entities(text, entities, area_id)
            if not matches:
                logger.warning(f"No matching HA entities found for command '{text}'.")
                return "Устройство не найдено"

            # Execute actions sequentially for each matched device
            speech_resp = matches[0].get("combined_speech_response") or matches[0].get("speech_response")
            for match in matches:
                logger.info(
                    f"Executing HA Action: {match['domain']}.{match['action']} -> "
                    f"{match['entity_id']} ({match['friendly_name']})"
                )
                await rest_client.send_action(match["domain"], match["action"], match["entity_id"])

            return speech_resp or "Команда выполнена"
        except Exception as ex:
            logger.error(f"Error processing HA voice command '{text}': {ex}")
            return "Устройство не найдено"

    async def close(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

