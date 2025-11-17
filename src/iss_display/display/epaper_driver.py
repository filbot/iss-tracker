"""Hardware abstraction for the GeeekPi 2.13 inch e-paper display."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from PIL import Image
else:
    from PIL import Image  # type: ignore

try:  # Optional hardware imports - defer errors until actually instantiating the driver.
    import spidev as _spidev  # type: ignore
    import RPi.GPIO as _GPIO  # type: ignore
except Exception:  # pragma: no cover - desktop environments will not have these modules.
    _spidev = None
    _GPIO = None

spidev: Any = _spidev
GPIO: Any = _GPIO

LOGGER = logging.getLogger(__name__)


class DisplayDriver(Protocol):
    """Protocol implemented by all display drivers."""

    def display_frame(self, red: bytes, black: bytes, *, image: Optional[Image.Image] = None) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class DriverPins:
    reset: int = 17
    dc: int = 25
    busy: int = 24
    cs: int = 8


class BusyWaitTimeout(TimeoutError):
    """Raised when the BUSY pin never releases within the allotted timeout."""

    def __init__(self, stage: str, timeout: float) -> None:
        super().__init__(f"E-paper busy pin remained low during {stage} for {timeout:.1f}s")
        self.stage = stage
        self.timeout = timeout


class HardwareEpaperDriver:
    """SPI/GPIO implementation for the physical panel."""

    def __init__(
        self,
        width: int,
        height: int,
        has_red: bool = True,
        max_speed_hz: int = 4_000_000,
        pins: DriverPins | None = None,
    ) -> None:
        if spidev is None or GPIO is None:
            raise RuntimeError("spidev and RPi.GPIO are required for hardware mode")

        self.width = width
        self.height = height
        self.has_red = has_red
        self.pins = pins or DriverPins()
        self._chunk_size = 4096

        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = max_speed_hz
        self.spi.mode = 0b00

        # Clean up any previous GPIO state
        try:
            GPIO.cleanup()
        except Exception:
            pass

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.pins.reset, GPIO.OUT)
        GPIO.setup(self.pins.dc, GPIO.OUT)
        GPIO.setup(self.pins.cs, GPIO.OUT)
        GPIO.setup(self.pins.busy, GPIO.IN)
        
        # Initialize CS high (inactive)
        GPIO.output(self.pins.cs, GPIO.HIGH)
        
        # Give display a moment to stabilize
        time.sleep(0.1)
        
        # Log initial BUSY pin state for diagnostics
        initial_busy = GPIO.input(self.pins.busy)
        LOGGER.info("Initial BUSY pin (GPIO %d) state: %s", self.pins.busy, "HIGH" if initial_busy else "LOW")

        self._reset_with_retry()
        self._full_init()

    # --- Low-level helpers -------------------------------------------------
    def _wait(self, *, stage: str, timeout: float = 30.0) -> None:
        """Wait for BUSY pin to go HIGH. Reduced timeout for faster failure."""
        start = time.monotonic()
        deadline = start + timeout
        while GPIO.input(self.pins.busy) == GPIO.LOW:
            elapsed = time.monotonic() - start
            if time.monotonic() >= deadline:
                LOGGER.error(
                    "Busy pin (GPIO %d) stuck LOW during %s after %.1fs. "
                    "Check hardware connection and display power.",
                    self.pins.busy, stage, elapsed
                )
                raise BusyWaitTimeout(stage, timeout)
            time.sleep(0.01)

    def _command(self, value: int) -> None:
        GPIO.output(self.pins.dc, GPIO.LOW)
        GPIO.output(self.pins.cs, GPIO.LOW)
        self.spi.xfer([value & 0xFF])
        GPIO.output(self.pins.cs, GPIO.HIGH)

    def _data(self, value: int) -> None:
        GPIO.output(self.pins.dc, GPIO.HIGH)
        GPIO.output(self.pins.cs, GPIO.LOW)
        self.spi.xfer([value & 0xFF])
        GPIO.output(self.pins.cs, GPIO.HIGH)

    def _reset_with_retry(self, attempts: int = 3) -> None:
        last_error: BusyWaitTimeout | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._reset_once()
                return
            except BusyWaitTimeout as exc:
                last_error = exc
                LOGGER.warning(
                    "Panel reset attempt %s/%s failed (%s); retrying",
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(1.0)
        if last_error:
            LOGGER.error("All panel reset attempts failed; giving up")
            raise last_error

    def _reset_once(self) -> None:
        """Hardware reset sequence for the e-paper display."""
        LOGGER.debug("Starting hardware reset sequence")
        GPIO.output(self.pins.reset, GPIO.HIGH)
        time.sleep(0.2)
        GPIO.output(self.pins.reset, GPIO.LOW)
        time.sleep(0.002)
        GPIO.output(self.pins.reset, GPIO.HIGH)
        time.sleep(0.2)
        LOGGER.debug("Waiting for BUSY pin to indicate ready state")
        self._wait(stage="panel-reset")
    
    def _full_init(self) -> None:
        """Full initialization sequence for 2.13" V4 display."""
        LOGGER.debug("Starting full display initialization")
        
        # Software reset
        self._command(0x12)
        self._wait(stage="post-soft-reset")
        
        # Driver output control
        self._command(0x01)
        self._data(0xF9)  # Height - 1 (250-1=249=0xF9)
        self._data(0x00)
        self._data(0x00)
        
        # Data entry mode
        self._command(0x11)
        self._data(0x03)  # X increment, Y increment
        
        # Set RAM X start/end positions
        self._command(0x44)
        self._data(0x00)
        self._data(0x0F)  # 15 (16*8-1 = 127 for width 122)
        
        # Set RAM Y start/end positions
        self._command(0x45)
        self._data(0x00)
        self._data(0x00)
        self._data(0xF9)  # 249
        self._data(0x00)
        
        # Border waveform control
        self._command(0x3C)
        self._data(0x05)
        
        # Display update control
        self._command(0x21)
        self._data(0x00)
        self._data(0x80)
        
        # Temperature sensor
        self._command(0x18)
        self._data(0x80)
        
        # Set RAM X counter
        self._command(0x4E)
        self._data(0x00)
        
        # Set RAM Y counter
        self._command(0x4F)
        self._data(0x00)
        self._data(0x00)
        
        self._wait(stage="post-init")
        LOGGER.info("Display initialization complete")

    def _transfer_bytes(self, payload: bytes) -> None:
        if not payload:
            return
        GPIO.output(self.pins.cs, GPIO.LOW)
        view = memoryview(payload)
        chunk_size = self._chunk_size
        offset = 0
        while offset < len(view):
            chunk = view[offset : offset + chunk_size]
            # spi.xfer2 expects a sequence of ints; chunk.tolist() keeps allocations bounded.
            data = chunk.tolist() if hasattr(chunk, "tolist") else list(chunk)
            self.spi.xfer2(data)
            offset += len(chunk)
        GPIO.output(self.pins.cs, GPIO.HIGH)

    # --- Public API --------------------------------------------------------
    def display_frame(self, red: bytes, black: bytes, *, image: Optional[Image.Image] = None) -> None:
        """Send the prepared frame buffers to the panel."""

        byte_length = int(self.width * self.height / 8)
        if len(black) != byte_length:
            raise ValueError(f"black buffer must be {byte_length} bytes, got {len(black)}")
        if self.has_red and len(red) != byte_length:
            raise ValueError(f"red buffer must be {byte_length} bytes, got {len(red)}")

        if self.has_red:
            self._write_channel(0x26, red)
        self._write_channel(0x24, black)
        self._update()

    def _write_channel(self, command: int, payload: bytes) -> None:
        self._command(command)
        GPIO.output(self.pins.dc, GPIO.HIGH)
        self._transfer_bytes(payload)

    def _update(self) -> None:
        """Trigger display update."""
        self._command(0x22)  # Display Update Control 2
        self._data(0xF7)     # Enable clock signal, enable CP, load temperature value, load LUT, display pattern
        self._command(0x20)  # Master Activation (trigger update)
        self._wait(stage="display-refresh")

    def clear(self) -> None:
        """Clear the display to white."""
        empty = bytes([0xFF]) * int(self.width * self.height / 8)
        if self.has_red:
            self._write_channel(0x26, empty)
        self._write_channel(0x24, empty)
        self._update()

    def close(self) -> None:
        try:
            self.spi.close()
        finally:
            if GPIO:
                GPIO.cleanup([self.pins.reset, self.pins.dc, self.pins.cs, self.pins.busy])

    # Context manager helpers
    def __enter__(self) -> "HardwareEpaperDriver":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.close()


class PreviewDriver:
    """Driver that writes preview PNGs instead of talking to hardware."""

    def __init__(self, preview_dir: Path, *, width: int, height: int) -> None:
        self.preview_dir = preview_dir
        self.width = width
        self.height = height
        self.preview_dir.mkdir(parents=True, exist_ok=True)

    def display_frame(self, red: bytes, black: bytes, *, image: Optional[Image.Image] = None) -> None:
        timestamp = int(time.time())
        output_path = self.preview_dir / f"frame-{timestamp}.png"
        preview_image = image or Image.new("RGB", (self.width, self.height), "white")
        preview_image.save(output_path)

    def close(self) -> None:
        return None


def build_driver(*, preview_only: bool, preview_dir: Path, width: int, height: int, has_red: bool) -> DisplayDriver:
    """Factory that selects either the hardware or preview implementation."""

    if preview_only:
        return PreviewDriver(preview_dir=preview_dir, width=width, height=height)

    try:
        return HardwareEpaperDriver(width=width, height=height, has_red=has_red)
    except RuntimeError:
        # Fall back to preview mode automatically if hardware modules are missing.
        return PreviewDriver(preview_dir=preview_dir, width=width, height=height)
