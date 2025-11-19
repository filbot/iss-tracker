"""Display drivers for the GeeekPi/Waveshare 2.13" panel."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, Protocol

from PIL import Image

LOGGER = logging.getLogger(__name__)

try:  # pragma: no cover - hardware import optional
    from iss_display.display.vendor import epd2in13 as _waveshare_module  # type: ignore
except Exception as e:  # pragma: no cover - hardware import optional
    LOGGER.warning(f"Failed to import vendor driver: {e}")
    _waveshare_module = None


class DisplayDriver(Protocol):
    def display_frame(self, red: bytes, black: bytes, *, image: Optional[Image.Image] = None) -> None:
        ...

    def close(self) -> None:
        ...


class WaveshareDriver:
    """Adapter around the vendor-supplied epd2in13.py module."""

    def __init__(self, width: int, height: int, *, has_red: bool) -> None:
        if _waveshare_module is None:
            raise RuntimeError(
                "epd2in13 driver is unavailable; ensure vendor dependencies are installed or use --preview-only"
            )

        self.width = width
        self.height = height
        self.has_red = has_red
        self._expected_length = ((width + 7) // 8) * height
        self._epd = _waveshare_module.EPD()

        self._hardware_length = ((self._epd.width + 7) // 8) * self._epd.height
        if self._expected_length != self._hardware_length:
            LOGGER.warning(
                "Configured frame buffer length (%s) does not match hardware expectation (%s); proceeding with hardware values",
                self._expected_length,
                self._hardware_length,
            )

        LOGGER.info("Initializing epd2in13 panel")
        if self._epd.init() != 0:
            raise RuntimeError("Failed to initialize epd2in13 panel")
        self._epd.Clear()
        if not self.has_red:
            LOGGER.warning("Red plane disabled; red pixels will render as black")

    def display_frame(self, red: bytes, black: bytes, *, image: Optional[Image.Image] = None) -> None:
        self._validate_length(red, "red", expected=self._hardware_length)
        self._validate_length(black, "black", expected=self._hardware_length)

        black_payload = bytearray(black)
        red_payload = bytearray(red)

        if not self.has_red:
            # Ensure red overlay stays white when hardware red channel is unused.
            red_payload[:] = b"\xFF" * len(red_payload)

        self._epd.display(black_payload, red_payload)

    def _validate_length(self, payload: bytes, label: str, *, expected: int) -> None:
        if len(payload) != expected:
            raise ValueError(f"{label} buffer must be {expected} bytes, got {len(payload)}")

    def close(self) -> None:
        try:
            self._epd.sleep()
        except Exception:  # pragma: no cover - hardware only
            LOGGER.warning("Failed to enter deep sleep", exc_info=True)


class PreviewDriver:
    """Writes PNG previews to disk instead of touching hardware."""

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

    def close(self) -> None:  # pragma: no cover - trivial
        return None


def build_driver(*, preview_only: bool, preview_dir: Path, width: int, height: int, has_red: bool) -> DisplayDriver:
    if preview_only:
        LOGGER.info("preview-only mode enabled; writing PNG output to %s", preview_dir)
        return PreviewDriver(preview_dir=preview_dir, width=width, height=height)

    if _waveshare_module is None:
        LOGGER.warning(
            "epd2in13 vendor driver not importable; defaulting to preview mode. Install the Waveshare dependencies or rerun with --preview-only"
        )
        return PreviewDriver(preview_dir=preview_dir, width=width, height=height)

    try:
        return WaveshareDriver(width=width, height=height, has_red=has_red)
    except RuntimeError:
        LOGGER.warning("Failed to initialize epd2in13 hardware; falling back to preview mode", exc_info=True)
        return PreviewDriver(preview_dir=preview_dir, width=width, height=height)
