"""
Unified Home Assistant Client Façade (Domain: HomeAssistant).
Combines HAConfig, HARestClient, and HAWebSocketClient into a single entry point.
"""
from typing import Dict, Any, Optional, List
from server.domain.homeassistant.config import HAConfig
from server.domain.homeassistant.rest import HARestClient
from server.domain.homeassistant.ws import HAWebSocketClient


class HomeAssistantClient:
    """
    Unified Home Assistant Client Façade.
    Delegates REST operations to HARestClient and WebSocket operations to HAWebSocketClient.
    """
    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 5.0
    ):
        self.config = HAConfig.from_env_or_args(url=url, token=token, timeout=timeout)
        self.rest = HARestClient(self.config)
        self.ws = HAWebSocketClient(self.config)

    @property
    def url(self) -> str:
        return self.config.ws_url

    @property
    def token(self) -> str:
        return self.config.token

    # Delegates to HAWebSocketClient
    async def connect(self) -> bool:
        return await self.ws.connect()

    async def process_conversation(self, text: str, device_id: str, area_id: str) -> str:
        return await self.ws.process_conversation(text, device_id, area_id, rest_client=self.rest)

    # Delegates to HARestClient
    async def entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        return await self.rest.get_state(entity_id)

    async def entities(self) -> List[Dict[str, Any]]:
        return await self.rest.get_all_states()

    async def actions(self) -> List[Dict[str, Any]]:
        return await self.rest.get_services()

    async def send_action(self, domain: str, action: str, entity_id: str, extra_data: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        return await self.rest.send_action(domain, action, entity_id, extra_data)

    async def execute_command(self, command: Any) -> bool:
        return await self.rest.execute_command(command)

    async def close(self):
        """Close both WebSocket connection and HTTP REST session."""
        await self.ws.close()
        await self.rest.close()
