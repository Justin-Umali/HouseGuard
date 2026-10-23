"""Person detection.

Uses MobileNet-SSD (OpenCV DNN) when the model files exist in ./models,
otherwise falls back to OpenCV's built-in HOG person detector (no download needed).
"""
import cv2
import numpy as np

from . import config

PROTO = config.MODEL_DIR / "MobileNetSSD_deploy.prototxt"
MODEL = config.MODEL_DIR / "MobileNetSSD_deploy.caffemodel"
PERSON_CLASS_ID = 15  # PASCAL VOC class index for "person"


class PersonDetector:
    def __init__(self):
        self.net = None
        self.hog = None
        if PROTO.exists() and MODEL.exists():
            try:
                self.net = cv2.dnn.readNetFromCaffe(str(PROTO), str(MODEL))
                self.mode = "MobileNet-SSD"
                return
            except cv2.error:
                self.net = None
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.mode = "HOG (fallback)"

    def detect(self, frame, ssd_conf: float, hog_thr: float):
        """Return list of (x1, y1, x2, y2, score) in the original frame's coordinates."""
        h, w = frame.shape[:2]
        if self.net is not None:
            blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
            self.net.setInput(blob)
            out = self.net.forward()
            boxes = []
            for i in range(out.shape[2]):
                score = float(out[0, 0, i, 2])
                if int(out[0, 0, i, 1]) == PERSON_CLASS_ID and score >= ssd_conf:
                    x1, y1, x2, y2 = (out[0, 0, i, 3:7] * np.array([w, h, w, h])).astype(int)
                    boxes.append((max(x1, 0), max(y1, 0), min(x2, w - 1), min(y2, h - 1), score))
            return boxes

        scale = 320.0 / w
        small = cv2.resize(frame, (320, int(h * scale)))
        rects, weights = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        boxes = []
        for (x, y, bw, bh), wt in zip(rects, np.ravel(weights)):
            if float(wt) >= hog_thr:
                boxes.append((int(x / scale), int(y / scale), int((x + bw) / scale), int((y + bh) / scale), float(wt)))
        return boxes
