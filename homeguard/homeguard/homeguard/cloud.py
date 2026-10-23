"""Optional Firebase (Firestore) sync.

Pushes events (with a small thumbnail) and a status heartbeat to Firestore so the
GitHub Pages dashboard (docs/) can show them from anywhere. Everything here is
best-effort: if Firebase is not configured or the network is down, detection and
the local website keep working normally.
"""
import base64
import queue
import threading
import time

import cv2

from . import config, settings

enabled = False
_db = None
_q: "queue.Queue[tuple]" = queue.Queue(maxsize=100)
_last_status = {"key": None, "ts": 0.0}
STATUS_EVERY = 60.0  # seconds between unchanged heartbeats (free plan: 20k writes/day)

# --- camera preview on the website ---
PREVIEW_EVERY = 2.0      # seconds between pictures while someone is watching
WATCH_FOR = 45.0         # website pings every ~20 s; keep sending this long after the last ping
_watch_until = 0.0
_frame_provider = None   # callable returning JPEG bytes (set by the engine)
_control_first = True    # the first control snapshot only reports old state: ignore it
_seq = 0


def init() -> bool:
    """Connect to Firestore using the service-account key. Returns True if active."""
    global enabled, _db
    if not config.FIREBASE_CRED:
        print("[cloud] FIREBASE_CRED not set -> cloud sync off")
        return False
    if not config.FIREBASE_CRED.exists():
        print(f"[cloud] key file not found: {config.FIREBASE_CRED} -> cloud sync off")
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        firebase_admin.initialize_app(credentials.Certificate(str(config.FIREBASE_CRED)))
        _db = firestore.client()
    except Exception as exc:
        print(f"[cloud] could not start Firebase ({type(exc).__name__}: {exc}) -> cloud sync off")
        return False
    enabled = True
    threading.Thread(target=_worker, daemon=True, name="cloud").start()
    threading.Thread(target=_preview_loop, daemon=True, name="cloud-preview").start()
    try:
        _db.collection("control").on_snapshot(_on_control)
    except Exception as exc:
        print(f"[cloud] could not listen for website commands ({exc}); preview and remote arm are off")
    print("[cloud] Firebase sync ON")
    return True


def set_frame_provider(fn) -> None:
    """The engine registers a function that returns a small JPEG of the latest camera frame."""
    global _frame_provider
    _frame_provider = fn


def _on_control(col_snapshot, changes, read_time) -> None:
    """Commands written by the website: 'preview' (someone is watching) and 'system' (arm/disarm)."""
    global _watch_until, _control_first
    first, _control_first = _control_first, False
    if first:
        return  # old commands from before this start-up
    for ch in changes:
        if ch.type.name == "REMOVED":
            continue
        data = ch.document.to_dict() or {}
        if ch.document.id == "preview":
            _watch_until = time.time() + WATCH_FOR
        elif ch.document.id == "system" and isinstance(data.get("armed"), bool):
            settings.save({"armed": data["armed"]})
            print(f"[cloud] website set armed={data['armed']}")


def _preview_loop() -> None:
    """While a person is watching on the website, upload a small picture every few seconds."""
    global _seq
    was_watching = False
    while True:
        time.sleep(PREVIEW_EVERY)
        watching = time.time() < _watch_until and _frame_provider is not None
        try:
            if watching:
                jpg = _frame_provider()
                if jpg:
                    _seq += 1
                    _db.collection("preview").document("current").set({
                        "img": "data:image/jpeg;base64," + base64.b64encode(jpg).decode(),
                        "ts": time.time(),
                        "seq": _seq,
                    })
            elif was_watching:
                _db.collection("preview").document("current").delete()  # don't leave an old picture behind
        except Exception as exc:
            print(f"[cloud] preview upload failed: {type(exc).__name__}: {exc}")
            time.sleep(5)
        was_watching = watching


def _thumb_data_uri(snapshot_name: str) -> str | None:
    img = cv2.imread(str(config.SNAP_DIR / snapshot_name))
    if img is None:
        return None
    h, w = img.shape[:2]
    img = cv2.resize(img, (320, max(int(h * 320 / w), 1)))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def _worker():
    while True:
        kind, payload = _q.get()
        try:
            if kind == "event":
                doc = {
                    "ts": payload["ts"],
                    "level": payload["level"],
                    "message": payload["message"],
                }
                if payload.get("snapshot"):
                    thumb = _thumb_data_uri(payload["snapshot"])
                    if thumb:
                        doc["thumb"] = thumb
                _db.collection("events").add(doc)
            elif kind == "status":
                _db.collection("status").document("current").set(payload)
        except Exception as exc:
            print(f"[cloud] upload failed: {type(exc).__name__}: {exc}")
            time.sleep(5)


def push_event(level: int, message: str, snapshot_name: str | None) -> None:
    if not enabled:
        return
    try:
        _q.put_nowait(("event", {"ts": time.time(), "level": level,
                                 "message": message, "snapshot": snapshot_name}))
    except queue.Full:
        pass


def heartbeat(level: int, level_name: str, armed: bool, camera_ok: bool, detector: str) -> None:
    """Called every detection loop; only writes when something changed or once a minute."""
    if not enabled:
        return
    now = time.time()
    key = (level, armed, camera_ok)
    if key == _last_status["key"] and now - _last_status["ts"] < STATUS_EVERY:
        return
    _last_status.update(key=key, ts=now)
    try:
        _q.put_nowait(("status", {"level": level, "level_name": level_name, "armed": armed,
                                  "camera_ok": camera_ok, "detector": detector, "updated": now}))
    except queue.Full:
        pass
