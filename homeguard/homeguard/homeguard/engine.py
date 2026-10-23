"""The 3-level detection pipeline.

Level 0  Idle       nothing happening
Level 1  Camera     the AI sees a person on camera
Level 2  Confirmed  the PIR motion sensor agrees (within the confirm window)
Level 3  DANGER     the confirmed presence lasts `danger_seconds` -> alert homeowners
"""
import threading
import time
from pathlib import Path

import cv2

from . import cloud, config, db, notifier, settings
from .camera import Camera
from .detector import PersonDetector
from .pir import PIR

LEVEL_NAMES = {0: "Idle", 1: "Person seen", 2: "Motion confirmed", 3: "DANGER"}
LEVEL_LOG_GAP = 30  # seconds between repeated Level 1 / 2 log entries


class Engine(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="engine")
        db.init()
        self.camera = Camera()
        self.detector = PersonDetector()
        self.pir = PIR()
        self._halt = threading.Event()
        self._lock = threading.Lock()

        self.level = 0
        self.boxes: list = []
        self.person_now = False
        self.last_person = 0.0
        self.person_since: float | None = None
        self.last_alert = 0.0
        self._last_logged = {1: 0.0, 2: 0.0}
        self.fps = 0.0

    # ---------- helpers ----------
    def _save_snapshot(self, frame, boxes, level: int) -> str:
        img = self._annotate(frame.copy(), boxes, level)
        name = time.strftime("%Y%m%d-%H%M%S") + f"-L{level}.jpg"
        cv2.imwrite(str(config.SNAP_DIR / name), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        self._prune_snapshots()
        return name

    @staticmethod
    def _prune_snapshots(keep: int = 300) -> None:
        files = sorted(config.SNAP_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        for old in files[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass

    @staticmethod
    def _annotate(frame, boxes, level: int):
        color = {0: (0, 200, 0), 1: (0, 200, 255), 2: (0, 140, 255), 3: (0, 0, 255)}[level]
        for (x1, y1, x2, y2, score) in boxes:
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"person {score:.2f}", (x1, max(y1 - 6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 24), (0, 0, 0), -1)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, f"L{level} {LEVEL_NAMES[level]}  {stamp}", (6, 17),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        return frame

    # ---------- public API for the web app ----------
    def annotated_jpeg(self) -> bytes | None:
        frame = self.camera.read()
        if frame is None:
            return None
        with self._lock:
            boxes, level = list(self.boxes), self.level
        ok, buf = cv2.imencode(".jpg", self._annotate(frame, boxes, level), [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def preview_jpeg(self, width: int = 320, quality: int = 55) -> bytes | None:
        """Small annotated picture for the cloud preview (about 10-15 KB)."""
        frame = self.camera.read()
        if frame is None:
            return None
        with self._lock:
            boxes, level = list(self.boxes), self.level
        img = self._annotate(frame, boxes, level)
        h, w = img.shape[:2]
        img = cv2.resize(img, (width, max(int(h * width / w), 1)))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def status(self) -> dict:
        s = settings.load()
        with self._lock:
            return {
                "level": self.level,
                "level_name": LEVEL_NAMES[self.level],
                "person": self.person_now,
                "motion": self.pir.motion,
                "armed": s["armed"],
                "camera_ok": self.camera.ok,
                "pir_hardware": self.pir.hardware,
                "detector": self.detector.mode,
                "email_ready": notifier.smtp_configured() and bool(s["recipients"]),
                "last_alert": self.last_alert or None,
                "fps": round(self.fps, 1),
            }

    def stop(self):
        self._halt.set()
        self.camera.stop()

    # ---------- main loop ----------
    def run(self):
        cloud.set_frame_provider(self.preview_jpeg)
        cloud.init()
        self.camera.start()
        last_tick = time.time()
        while not self._halt.is_set():
            s = settings.load()
            time.sleep(max(float(s["detect_interval"]), 0.1))
            frame = self.camera.read()
            if frame is None:
                continue

            now = time.time()
            self.fps = 1.0 / max(now - last_tick, 1e-3)
            last_tick = now

            boxes = self.detector.detect(frame, float(s["ssd_confidence"]), float(s["hog_threshold"]))
            person = bool(boxes)
            motion = self.pir.motion
            window = float(s["confirm_window"])

            if person:
                self.last_person = now
                if self.person_since is None:
                    self.person_since = now
            elif now - self.last_person > window:
                self.person_since = None

            level1 = (now - self.last_person) <= window and self.last_person > 0
            level2 = level1 and (now - self.pir.last_motion) <= window and self.pir.last_motion > 0
            if not s["require_pir"]:
                level2 = level1  # PIR optional: camera alone can confirm
            present_for = (now - self.person_since) if self.person_since else 0.0
            level3 = level2 and present_for >= float(s["danger_seconds"])

            level = 3 if level3 else 2 if level2 else 1 if level1 else 0
            if not s["armed"]:
                level = 0

            with self._lock:
                self.level, self.boxes, self.person_now = level, boxes, person
            cloud.heartbeat(level, LEVEL_NAMES[level], bool(s["armed"]), self.camera.ok, self.detector.mode)

            if level in (1, 2) and now - self._last_logged[level] > LEVEL_LOG_GAP:
                self._last_logged[level] = now
                snap = self._save_snapshot(frame, boxes, level)
                msg = ("Camera detected a person." if level == 1
                       else "Camera and PIR motion sensor both confirm presence.")
                db.add_event(level, msg, snap)
                cloud.push_event(level, msg, snap)

            if level == 3 and now - self.last_alert > float(s["cooldown"]):
                self.last_alert = now
                snap = self._save_snapshot(frame, boxes, 3)
                detail = (f"Person present for {present_for:.0f}s, confirmed by "
                          f"{'camera + PIR' if s['require_pir'] else 'camera'}.")
                event_id = db.add_event(3, "DANGER: " + detail, snap)
                cloud.push_event(3, "DANGER: " + detail, snap)
                notifier.send_alert_async(event_id, s["recipients"], Path(config.SNAP_DIR / snap), detail)
