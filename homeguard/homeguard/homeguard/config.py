"""Central configuration. Values come from environment variables or a .env file."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SNAP_DIR = DATA_DIR / "snapshots"
MODEL_DIR = BASE_DIR / "models"
DB_PATH = DATA_DIR / "homeguard.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

for _d in (DATA_DIR, SNAP_DIR, MODEL_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# --- Camera / sensor ---
CAMERA_INDEX = _int("CAMERA_INDEX", 0)
FRAME_WIDTH = _int("FRAME_WIDTH", 640)
FRAME_HEIGHT = _int("FRAME_HEIGHT", 480)
PIR_PIN = _int("PIR_PIN", 17)  # BCM numbering (GPIO17 = physical pin 11)

# --- Web ---
HOST = os.environ.get("HG_HOST", "0.0.0.0")
PORT = _int("HG_PORT", 5000)
ADMIN_PASSWORD = os.environ.get("HG_PASSWORD", "")

_secret_file = DATA_DIR / ".secret_key"
if os.environ.get("HG_SECRET_KEY"):
    SECRET_KEY = os.environ["HG_SECRET_KEY"]
else:
    if not _secret_file.exists():
        _secret_file.write_text(secrets.token_hex(32))
        try:
            _secret_file.chmod(0o600)
        except OSError:
            pass
    SECRET_KEY = _secret_file.read_text().strip()

# --- Firebase (optional cloud sync for the GitHub Pages dashboard) ---
_cred = os.environ.get("FIREBASE_CRED", "").strip()
FIREBASE_CRED = (BASE_DIR / _cred) if _cred and not os.path.isabs(_cred) else (Path(_cred) if _cred else None)

# --- Email (SMTP) ---
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _int("SMTP_PORT", 587)  # 587 = STARTTLS, 465 = SSL
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
ALERT_TO = os.environ.get("ALERT_TO", "")  # comma separated default recipients
