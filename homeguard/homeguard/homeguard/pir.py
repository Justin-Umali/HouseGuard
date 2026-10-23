"""PIR motion sensor (HC-SR501 or similar) with a safe fallback for testing off the Pi."""
import threading
import time

from . import config

try:
    from gpiozero import MotionSensor
except Exception:  # not on a Pi, or gpiozero missing
    MotionSensor = None


class PIR:
    def __init__(self):
        self.last_motion = 0.0
        self._sim_until = 0.0
        self._lock = threading.Lock()
        self.hardware = False
        self._sensor = None
        if MotionSensor is not None:
            try:
                self._sensor = MotionSensor(config.PIR_PIN, queue_len=1, sample_rate=10)
                self._sensor.when_motion = self._on_motion
                self.hardware = True
            except Exception as exc:  # pin factory unavailable, etc.
                print(f"[pir] hardware unavailable ({exc}); using simulation only")

    def _on_motion(self):
        with self._lock:
            self.last_motion = time.time()

    @property
    def motion(self) -> bool:
        """True while the sensor output is HIGH (or a simulated pulse is active)."""
        if time.time() < self._sim_until:
            return True
        if self._sensor is not None:
            try:
                active = bool(self._sensor.motion_detected)
            except Exception:
                active = False
            if active:
                self._on_motion()
            return active
        return False

    def simulate(self, seconds: float = 10.0):
        """Pretend the PIR fired (for testing from the website without hardware)."""
        self._sim_until = time.time() + seconds
        self._on_motion()
