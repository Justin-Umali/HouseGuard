"""Runtime settings editable from the website, persisted as JSON."""
import json
import threading

from . import config

DEFAULTS = {
    "armed": True,
    "recipients": config.ALERT_TO,   # comma separated
    "ssd_confidence": 0.55,          # MobileNet-SSD person confidence
    "hog_threshold": 0.7,            # HOG fallback score threshold
    "confirm_window": 8,             # seconds camera + PIR may be apart and still confirm
    "danger_seconds": 3,             # confirmed presence must last this long -> Level 3
    "cooldown": 120,                 # minimum seconds between alert emails
    "require_pir": True,             # Level 3 needs the PIR to agree with the camera
    "detect_interval": 0.6,          # seconds between AI detections (CPU saver)
}

_lock = threading.Lock()


def load() -> dict:
    data = dict(DEFAULTS)
    if config.SETTINGS_PATH.exists():
        try:
            data.update(json.loads(config.SETTINGS_PATH.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return data


def save(updates: dict) -> dict:
    with _lock:
        data = load()
        for key, value in updates.items():
            if key in DEFAULTS:
                data[key] = value
        config.SETTINGS_PATH.write_text(json.dumps(data, indent=2))
        return data
