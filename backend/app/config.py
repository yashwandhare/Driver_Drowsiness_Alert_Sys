import json
import os
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "system_config.json"

DEFAULTS: Dict[str, Any] = {
    "camera": {
        "model": "OV3660",
        "stream_format": "MJPEG",
        "target_fps": 12,
        "frame_size": "VGA",
        "jpeg_quality": 12,
        "xclk_hz": 20_000_000,
    },
    "network": {
        "server_host": "127.0.0.1",
        "backend_port": 8000,
        "frontend_port": 5500,
        "status_poll_ms": 500,
        "frame_interval_ms": 100,
        "api_token": "",
    },
    "detection": {
        "ear_close_threshold": 0.23,
        "ear_open_threshold": 0.26,
        "drowsy_frames": 20,
        "show_display": False,
    },
    "alerts": {
        "buzzer_gpio": 13,
        "status_led_gpio": 4,
        "drowsy_blink_ms": 120,
        "connected_led_mode": "solid_on",
        "disconnected_led_mode": "off",
    },
}


def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> Dict[str, Any]:
    config_path = Path(os.getenv("DRIVER_DROWSINESS_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        return dict(DEFAULTS)

    with config_path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    return _merge_dict(DEFAULTS, loaded)


SETTINGS = load_config()
