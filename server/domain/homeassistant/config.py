"""
Home Assistant Configuration Value Object (Domain: HomeAssistant).
Encapsulates connection parameters and URL normalization logic.
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class HAConfig:
    """Encapsulates Home Assistant connection settings and URL normalization."""
    raw_url: str
    token: str
    timeout: float = 5.0

    @classmethod
    def from_env_or_args(
        cls,
        url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 5.0
    ) -> "HAConfig":
        env_url = url or os.getenv("HA_URL") or os.getenv("HA_WS_URL") or "http://localhost:8123"
        env_token = token or os.getenv("HA_TOKEN") or "MOCK_LONG_LIVED_ACCESS_TOKEN"
        return cls(raw_url=env_url, token=env_token, timeout=timeout)

    @property
    def http_base_url(self) -> str:
        """Derive clean HTTP/HTTPS base URL without trailing slash or /api/websocket."""
        clean = self.raw_url.rstrip("/")
        if clean.endswith("/api/websocket"):
            clean = clean.rsplit("/api/websocket", 1)[0]
        if clean.startswith("ws://"):
            return clean.replace("ws://", "http://", 1)
        elif clean.startswith("wss://"):
            return clean.replace("wss://", "https://", 1)
        elif not clean.startswith("http://") and not clean.startswith("https://"):
            return f"http://{clean}"
        return clean

    @property
    def ws_url(self) -> str:
        """Derive clean WebSocket WS/WSS URL ending with /api/websocket."""
        clean = self.raw_url.rstrip("/")
        if not clean.endswith("/api/websocket"):
            clean = f"{clean}/api/websocket"
        if clean.startswith("http://"):
            return clean.replace("http://", "ws://", 1)
        elif clean.startswith("https://"):
            return clean.replace("https://", "wss://", 1)
        elif not clean.startswith("ws://") and not clean.startswith("wss://"):
            return f"ws://{clean}"
        return clean
