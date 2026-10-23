"""Threaded USB camera reader that always exposes the newest frame (no buffer lag)."""
import threading
import time

import cv2

from . import config


class Camera(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="camera")
        self._lock = threading.Lock()
        self._frame = None
        self._halt = threading.Event()
        self.ok = False

    def _open(self):
        cap = cv2.VideoCapture(config.CAMERA_INDEX, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(config.CAMERA_INDEX)  # non-Linux fallback (testing on a PC)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # much lighter on the Pi's USB/CPU
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def run(self):
        cap = None
        while not self._halt.is_set():
            if cap is None or not cap.isOpened():
                self.ok = False
                if cap is not None:
                    cap.release()
                cap = self._open()
                if not cap.isOpened():
                    time.sleep(3)
                    continue
            good, frame = cap.read()
            if not good:
                self.ok = False
                cap.release()
                cap = None
                time.sleep(1)
                continue
            self.ok = True
            with self._lock:
                self._frame = frame
        if cap is not None:
            cap.release()

    def read(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def stop(self):
        self._halt.set()
