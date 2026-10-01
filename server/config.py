"""
Central Server Environment Configuration Loader.
Parses .env files and environment variables into typed ServerConfig objects.
"""
import os
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("ServerConfig")


def load_dotenv(env_filename: str = ".env"):
    """
    Parse key-value pairs from .env file into os.environ if not already set.
    Zero-dependency standalone implementation.
    """
    candidates = [
        env_filename,
        os.path.join(os.getcwd(), env_filename),
        os.path.join(os.path.dirname(__file__), "..", env_filename),
        os.path.join(os.path.dirname(__file__), env_filename),
    ]

    target_path = None
    for candidate in candidates:
        if os.path.exists(candidate) and os.path.isfile(candidate):
            target_path = candidate
            break

    if not target_path:
        return

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = value
    except Exception as e:
        logger.warning(f"Failed to parse .env file at {target_path}: {e}")


def update_dotenv_var(key: str, value: str, env_filename: str = ".env"):
    """
    Update or append key=value pair in .env file and update os.environ.
    """
    os.environ[key] = str(value)
    candidates = [
        env_filename,
        os.path.join(os.getcwd(), env_filename),
        os.path.join(os.path.dirname(__file__), "..", env_filename),
        os.path.join(os.path.dirname(__file__), env_filename),
    ]
    target_path = None
    for candidate in candidates:
        if os.path.exists(candidate) and os.path.isfile(candidate):
            target_path = candidate
            break
    if not target_path:
        target_path = os.path.join(os.getcwd(), env_filename)

    lines = []
    found = False
    if os.path.exists(target_path):
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning(f"Could not read .env at {target_path}: {e}")

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            if k.strip() == key:
                new_lines.append(f"{key}={value}\n")
                found = True
                continue
        new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.warning(f"Could not write .env to {target_path}: {e}")


# Load environment variables on module import
load_dotenv()


@dataclass
class ServerConfig:
    """Dataclass encapsulating Central Voice Server configuration parameters."""
    host: str = "0.0.0.0"
    port: int = 8765
    log_level: str = "INFO"
    log_file: str = "logs/server.log"
    ha_url: str = "http://localhost:8123"
    ha_token: str = "MOCK_LONG_LIVED_ACCESS_TOKEN"
    ha_timeout: float = 5.0
    arbiter_window_ms: float = 400.0
    arbiter_cooldown_sec: float = 1.5
    denoise_enabled: bool = True
    noise_suppression_level: float = 0.85
    gain_multiplier: float = 1.0
    agc_target_rms: float = 3500.0
    hpf_cutoff_hz: float = 80.0
    agc_max_gain: float = 8.0
    vad_silence_ms: int = 800
    vad_threshold: float = 0.5

    @classmethod
    def from_env(cls) -> "ServerConfig":
        load_dotenv()
        denoise_str = os.getenv("DENOISE_ENABLED", "true").lower()
        return cls(
            host=os.getenv("SERVER_HOST", "0.0.0.0"),
            port=int(os.getenv("SERVER_PORT", "8765")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=os.getenv("LOG_FILE", "logs/server.log"),
            ha_url=os.getenv("HA_URL") or os.getenv("HA_WS_URL") or "http://localhost:8123",
            ha_token=os.getenv("HA_TOKEN", "MOCK_LONG_LIVED_ACCESS_TOKEN"),
            ha_timeout=float(os.getenv("HA_TIMEOUT", "5.0")),
            arbiter_window_ms=float(os.getenv("ARBITER_WINDOW_MS", "400.0")),
            arbiter_cooldown_sec=float(os.getenv("ARBITER_COOLDOWN_SEC", "1.5")),
            denoise_enabled=denoise_str in ("true", "1", "yes"),
            noise_suppression_level=float(os.getenv("NOISE_SUPPRESSION_LEVEL", "0.85")),
            gain_multiplier=float(os.getenv("GAIN_MULTIPLIER", "1.0")),
            agc_target_rms=float(os.getenv("AGC_TARGET_RMS", "3500.0")),
            hpf_cutoff_hz=float(os.getenv("HPF_CUTOFF_HZ", "80.0")),
            agc_max_gain=float(os.getenv("AGC_MAX_GAIN", "8.0")),
            vad_silence_ms=int(os.getenv("VAD_SILENCE_MS", "800")),
            vad_threshold=float(os.getenv("VAD_THRESHOLD", "0.5")),
        )


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = "logs/server.log"):
    """
    Configure root logging with Console (stdout) and RotatingFileHandler.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid adding duplicate handlers if re-initialized
    if root_logger.handlers:
        root_logger.handlers.clear()

    # 1. Console Stream Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)

    # 2. Rotating File Handler
    if log_file:
        try:
            log_dir = os.path.dirname(os.path.abspath(log_file))
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            from logging.handlers import RotatingFileHandler
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
                encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            root_logger.addHandler(file_handler)
        except Exception as e:
            root_logger.warning(f"Failed to initialize file logging to '{log_file}': {e}")

