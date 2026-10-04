"""
Satellite Communication Protocol Definition (Domain: Satellite).
Data contracts for satellite initialization and Central Server control signals.
"""
import json
from dataclasses import dataclass
from typing import Optional, Union, Dict, Any


@dataclass
class InitMessage:
    device_id: str
    area_id: str
    rms: float
    mic_gain: float = 1.0
    speaker_volume: float = 1.0
    led_brightness: int = 255
    vad_multiplier: float = 3.5
    dc_removal_enabled: bool = True
    noise_tracking_enabled: bool = True
    mic_gain_enabled: bool = True
    speaker_volume_enabled: bool = True
    wakenet_threshold: int = 400
    wakenet_mode: int = 0
    cooldown_ms: int = 500
    mic_shift: int = 14
    stream_max_ms: int = 10000
    stream_silence_end_ms: int = 1500
    mic_slot: int = -1

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InitMessage":
        msg_type = data.get("event") or data.get("type")
        if msg_type != "init":
            raise ValueError(f"Invalid event/type: {msg_type}")
        if "device_id" not in data or "area_id" not in data:
            raise ValueError("Missing required fields in INIT message (device_id, area_id)")

        rms_val = data.get("rms")
        if rms_val is None:
            rms_val = max(float(data.get("rms_left", 0.0)), float(data.get("rms_right", 0.0)))

        return cls(
            device_id=str(data["device_id"]),
            area_id=str(data["area_id"]),
            rms=float(rms_val),
            mic_gain=float(data.get("mic_gain", 1.0)),
            speaker_volume=float(data.get("speaker_volume", 1.0)),
            led_brightness=int(data.get("led_brightness", 255)),
            vad_multiplier=float(data.get("vad_multiplier", 3.5)),
            dc_removal_enabled=bool(data.get("dc_removal_enabled", True)),
            noise_tracking_enabled=bool(data.get("noise_tracking_enabled", True)),
            mic_gain_enabled=bool(data.get("mic_gain_enabled", True)),
            speaker_volume_enabled=bool(data.get("speaker_volume_enabled", True)),
            wakenet_threshold=int(data.get("wakenet_threshold", 400)),
            wakenet_mode=int(data.get("wakenet_mode", 0)),
            cooldown_ms=int(data.get("cooldown_ms", 500)),
            mic_shift=int(data.get("mic_shift", 14)),
            stream_max_ms=int(data.get("stream_max_ms", 10000)),
            stream_silence_end_ms=int(data.get("stream_silence_end_ms", 1500)),
            mic_slot=int(data.get("mic_slot", -1))
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "init",
            "event": "init",
            "device_id": self.device_id,
            "area_id": self.area_id,
            "rms": self.rms,
            "mic_gain": self.mic_gain,
            "speaker_volume": self.speaker_volume,
            "led_brightness": self.led_brightness,
            "vad_multiplier": self.vad_multiplier,
            "dc_removal_enabled": self.dc_removal_enabled,
            "noise_tracking_enabled": self.noise_tracking_enabled,
            "mic_gain_enabled": self.mic_gain_enabled,
            "speaker_volume_enabled": self.speaker_volume_enabled,
            "wakenet_threshold": self.wakenet_threshold,
            "wakenet_mode": self.wakenet_mode,
            "cooldown_ms": self.cooldown_ms,
            "mic_shift": self.mic_shift,
            "stream_max_ms": self.stream_max_ms,
            "stream_silence_end_ms": self.stream_silence_end_ms,
            "mic_slot": self.mic_slot
        }


@dataclass
class ControlMessage:
    action: str  # "continue", "stop", "start_debug", "stop_debug", or "update_config"
    reason: Optional[str] = None

    def to_json(self) -> str:
        data = {"action": self.action}
        if self.reason:
            data["reason"] = self.reason
        return json.dumps(data)


@dataclass
class ConfigUpdateMessage:
    config: Dict[str, Any]
    action: str = "update_config"

    def to_json(self) -> str:
        return json.dumps({
            "action": self.action,
            "config": self.config
        })


def parse_incoming_message(data: Union[str, bytes]) -> Union[InitMessage, bytes, Dict[str, Any]]:
    """Parse WebSocket frame. Returns InitMessage for JSON init, raw bytes for audio PCM stream."""
    if isinstance(data, bytes):
        return data  # Raw PCM audio binary frame
    
    try:
        payload = json.loads(data)
        if isinstance(payload, dict):
            msg_type = payload.get("event") or payload.get("type")
            if msg_type == "init":
                return InitMessage.from_dict(payload)
        return payload
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON payload: {data}") from e
