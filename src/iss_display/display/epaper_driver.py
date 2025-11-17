"""Display drivers for the GeeekPi/Waveshare 2.13" panel."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, Protocol

from PIL import Image

try:
    from waveshare_epd import epd2in13b_V4 as _waveshare_module  # type: ignore
except Exception:  # pragma: no cover - hardware import optional
    _waveshare_module = None

LOGGER = logging.getLogger(__name__)


class DisplayDriver(Protocol):
    def display_frame(self, red: bytes, black: bytes, *, image: Optional[Image.Image] = None) -> None:
        ...

    def close(self) -> None:
        ...


class WaveshareDriver:
    """Thin wrapper around the official waveshare_epd driver."""

    def __init__(self, width: int, height: int, *, has_red: bool) -> None:
        if _waveshare_module is None:
            raise RuntimeError("waveshare_epd package not available; re-run with --preview-only or install the vendor driver")

        self.width = width
        self.height = height
        self.has_red = has_red
        self._byte_length = (width * height) // 8
        self._blank_red = bytes([0xFF] * self._byte_length)
        self._epd = _waveshare_module.EPD()

        LOGGER.info("Initializing epd2in13b_V4 panel")
        self._epd.init()
        self._epd.Clear()

    def display_frame(self, red: bytes, black: bytes, *, image: Optional[Image.Image] = None) -> None:
        self._validate_length(black, "black")
        if self.has_red:
            self._validate_length(red, "red")
            red_payload = bytearray(red)
        else:
            red_payload = bytearray(self._blank_red)
        black_payload = bytearray(black)
        self._epd.display(black_payload, red_payload)

    def _validate_length(self, payload: bytes, label: str) -> None:
        if len(payload) != self._byte_length:
            raise ValueError(f"{label} buffer must be {self._byte_length} bytes, got {len(payload)}")

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
            "waveshare_epd package not available; defaulting to preview driver. Install the vendor driver or rerun with --preview-only"
        )
        return PreviewDriver(preview_dir=preview_dir, width=width, height=height)

    try:
        return WaveshareDriver(width=width, height=height, has_red=has_red)
    except RuntimeError:
        LOGGER.warning("Failed to initialize hardware driver; falling back to preview mode", exc_info=True)
        return PreviewDriver(preview_dir=preview_dir, width=width, height=height)
