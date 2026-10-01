"""
Home Assistant HTTP REST API Client Component (Domain: HomeAssistant).
Handles fetching entity states, listing services, sending actions, and command execution.
"""
import logging
from typing import Dict, Any, Optional, List
import aiohttp
from server.domain.homeassistant.config import HAConfig

logger = logging.getLogger("HARestClient")


class HARestClient:
    """Handles HTTP REST API calls (fetching entity states, listing services, sending actions)."""
    def __init__(self, config: HAConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
            }
            timeout_cfg = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(headers=headers, timeout=timeout_cfg)
        return self._session

    async def request(self, method: str = "GET", path: str = "", data: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """Generic HTTP REST request with error handling."""
        try:
            session = await self._get_session()
            url = f"{self.config.http_base_url}{path}"
            async with session.request(method, url, json=data) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                logger.error(f"HA REST [{method} {path}] returned HTTP {resp.status}")
                return None
        except Exception as e:
            logger.warning(f"HA REST error [{method} {path}]: {e}")
            return None

    async def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        return await self.request("GET", f"/api/states/{entity_id}")

    async def get_all_states(self) -> List[Dict[str, Any]]:
        res = await self.request("GET", "/api/states")
        return res if isinstance(res, list) else []

    async def get_services(self) -> List[Dict[str, Any]]:
        res = await self.request("GET", "/api/services")
        return res if isinstance(res, list) else []

    async def send_action(self, domain: str, action: str, entity_id: str, extra_data: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        payload = {"entity_id": entity_id}
        if extra_data:
            payload.update(extra_data)
        return await self.request("POST", f"/api/services/{domain}/{action}", data=payload)

    async def execute_command(self, command: Any) -> bool:
        """Execute a parsed Command dataclass/object."""
        if not command or not getattr(command, "object", None) or not getattr(command, "action", None):
            logger.warning("Invalid command object provided for execution.")
            return False

        entity_id = getattr(command.object, "code", "")
        action = getattr(command.action, "code", "")
        if not entity_id or not action:
            logger.warning(f"Incomplete command object: entity='{entity_id}', action='{action}'")
            return False

        domain = entity_id.split('.')[0] if '.' in entity_id else "homeassistant"
        logger.info(f"Executing HA command: domain='{domain}', action='{action}', entity='{entity_id}'")
        result = await self.send_action(domain, action, entity_id)
        return result is not None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
