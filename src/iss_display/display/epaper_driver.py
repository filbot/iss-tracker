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

try:  # Prefer the official Waveshare driver when available.
    from waveshare_epd import epd2in13b_V4 as _waveshare_module  # type: ignore
except Exception:  # pragma: no cover - deployment without the vendor package falls back to custom SPI.
    _waveshare_module = None

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


class WaveshareEpaperDriver:
    """Thin wrapper around Waveshare's reference driver.

    Delegating to the vendor driver ensures the initialization sequence, refresh
    waveform, and busy timing exactly match the working demo script the user provided.
    """

    def __init__(self, width: int, height: int, *, has_red: bool = True) -> None:
        if _waveshare_module is None:
            raise RuntimeError("waveshare_epd package not available")

        self.width = width
        self.height = height
        self.has_red = has_red
        self._row_bytes = (width + 7) // 8
        self._byte_length = self._row_bytes * height
        self._epd = _waveshare_module.EPD()
        self._blank_red = bytearray([0xFF] * self._byte_length)

        LOGGER.info("Initializing waveshare_epd.epd2in13b_V4 driver")
        self._epd.init()
        self._epd.Clear()

    def _ensure_length(self, payload: bytes, label: str) -> None:
        if len(payload) != self._byte_length:
            raise ValueError(f"{label} buffer must be {self._byte_length} bytes, got {len(payload)}")

    def _buffer(self, payload: bytes | bytearray) -> bytearray:
        return payload if isinstance(payload, bytearray) else bytearray(payload)

    def display_frame(self, red: bytes, black: bytes, *, image: Optional[Image.Image] = None) -> None:
        self._ensure_length(black, "black")
        if self.has_red:
            self._ensure_length(red, "red")
            red_payload = self._buffer(red)
        else:
            red_payload = self._blank_red
        black_payload = self._buffer(black)

        # The vendor API expects black first, red second just like the sample script.
        self._epd.display(black_payload, red_payload)

    def close(self) -> None:
        try:
            self._epd.sleep()
        except Exception:  # pragma: no cover - hardware only
            LOGGER.exception("Failed to put e-paper panel into deep sleep")


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

    if _waveshare_module is not None:
        try:
            return WaveshareEpaperDriver(width=width, height=height, has_red=has_red)
        except Exception as exc:  # pragma: no cover - hardware-only path
            LOGGER.warning("waveshare_epd driver init failed, falling back to SPI implementation: %s", exc)

    try:
        return HardwareEpaperDriver(width=width, height=height, has_red=has_red)
    except RuntimeError:
        # Fall back to preview mode automatically if hardware modules are missing.
        return PreviewDriver(preview_dir=preview_dir, width=width, height=height)
