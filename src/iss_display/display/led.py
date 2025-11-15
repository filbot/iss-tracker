"""GPIO-backed heartbeat LED controller."""

from __future__ import annotations

import logging
import random
import threading
from typing import Any, Optional

try:  # Optional dependency – present only on Raspberry Pi hardware
    import RPi.GPIO as _GPIO  # type: ignore
except Exception:  # pragma: no cover - desktop development envs
    _GPIO = None

GPIO: Any = _GPIO
LOGGER = logging.getLogger(__name__)


class LEDController:
    """Drives a single GPIO LED with heartbeat + busy patterns."""

    def __init__(self, pin: int) -> None:
        if GPIO is None:  # pragma: no cover - guarded by build helper
            raise RuntimeError("RPi.GPIO is not available")

        self._pin = pin
        self._mode = "heartbeat"
        self._running = True
        self._mode_event = threading.Event()

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self._pin, GPIO.OUT)
        GPIO.output(self._pin, GPIO.LOW)

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        mode = "busy" if busy else "heartbeat"
        if mode == self._mode:
            return
        self._mode = mode
        self._mode_event.set()

    def close(self) -> None:
        if GPIO is None:
            return
        self._running = False
        self._mode_event.set()
        if hasattr(self, "_thread"):
            self._thread.join(timeout=1)
        GPIO.output(self._pin, GPIO.LOW)
        GPIO.cleanup(self._pin)

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while self._running:
            if self._mode == "busy":
                if self._pattern_step(0.05, True):
                    continue
                if self._pattern_step(0.05, False):
                    continue
            else:
                on_window = random.uniform(0.05, 0.2)
                off_window = random.uniform(0.05, 0.55)
                if self._pattern_step(on_window, True):
                    continue
                if self._pattern_step(off_window, False):
                    continue
        GPIO.output(self._pin, GPIO.LOW)

    def _pattern_step(self, duration: float, state: bool) -> bool:
        if not self._running:
            return True
        GPIO.output(self._pin, GPIO.HIGH if state else GPIO.LOW)
        return self._wait(duration)

    def _wait(self, duration: float) -> bool:
        triggered = self._mode_event.wait(duration)
        if triggered:
            self._mode_event.clear()
        return triggered


def build_led_controller(enabled: bool, pin: int) -> Optional[LEDController]:
    """Factory that builds the LED controller when hardware is available."""

    if not enabled:
        return None
    if GPIO is None:
        LOGGER.warning("LED_ENABLED is true but RPi.GPIO is unavailable; disabling LED controller")
        return None
    try:
        return LEDController(pin)
    except RuntimeError as exc:  # pragma: no cover - defensive guard
        LOGGER.warning("Unable to initialize LED controller: %s", exc)
        return None


__all__ = ["LEDController", "build_led_controller"]
