"""Utilities to convert arbitrary images into tri-color (black/white/red) buffers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Union

from PIL import Image, ImageOps

try:  # Pillow >= 9 exposes Resampling
    _RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9 fallback
    _RESAMPLE = Image.LANCZOS


Buffer = Union[bytearray, list[int]]

# The hardware is mounted upside down in the enclosure, so rotate every frame 180°.
MOUNT_ROTATION_DEGREES = 180


class PanelDriver(Protocol):
    """Protocol describing what the EPD driver exposes to build buffers."""

    width: int
    height: int

    def getbuffer(self, image: Image.Image) -> Buffer:
        """Return a byte buffer ready to be sent to the panel."""

        ...


@dataclass(slots=True)
class TriColorFrame:
    """Container for the bitplanes the panel expects."""

    black: Buffer
    red: Buffer


def prepare_frame(
    source: Image.Image,
    panel: PanelDriver,
    *,
    black_threshold: int = 96,
    red_green_max: int = 80,
    red_blue_max: int = 80,
    red_min: int = 150,
) -> TriColorFrame:
    """Convert ``source`` into black and red bitplanes sized for ``panel``."""

    fitted = _fit_to_panel(source, panel)
    black_img, red_img = _split_planes(
        fitted,
        black_threshold=black_threshold,
        red_green_max=red_green_max,
        red_blue_max=red_blue_max,
        red_min=red_min,
    )
    return TriColorFrame(
        black=panel.getbuffer(black_img),
        red=panel.getbuffer(red_img),
    )


def _fit_to_panel(
    image: Image.Image,
    panel: PanelDriver,
) -> Image.Image:
    """Resize/rotate ``image`` so it matches the e-paper resolution."""

    portrait = (panel.width, panel.height)
    landscape = (panel.height, panel.width)
    working = image

    if working.size not in (portrait, landscape):
        working = ImageOps.fit(
            working.convert("RGB"),
            landscape,
            method=_RESAMPLE,
            centering=(0.5, 0.5),
        )
    elif working.mode != "RGB":
        working = working.convert("RGB")

    if MOUNT_ROTATION_DEGREES:
        working = working.rotate(MOUNT_ROTATION_DEGREES, expand=True)
    return working


def _split_planes(
    image: Image.Image,
    *,
    black_threshold: int,
    red_green_max: int,
    red_blue_max: int,
    red_min: int,
) -> tuple[Image.Image, Image.Image]:
    """Create black and red 1-bit images following simple color heuristics."""

    width, height = image.size
    black = Image.new("1", image.size, 1)
    red = Image.new("1", image.size, 1)

    src_px = image.load()
    black_px = black.load()
    red_px = red.load()

    for x in range(width):
        for y in range(height):
            r, g, b = src_px[x, y]
            if _is_red(r, g, b, red_min, red_green_max, red_blue_max):
                black_px[x, y] = 1
                red_px[x, y] = 0
            elif _is_black(r, g, b, black_threshold):
                black_px[x, y] = 0
                red_px[x, y] = 1
            else:
                black_px[x, y] = 1
                red_px[x, y] = 1
    return black, red


def _is_black(r: int, g: int, b: int, threshold: int) -> bool:
    return max(r, g, b) <= threshold


def _is_red(r: int, g: int, b: int, red_min: int, green_max: int, blue_max: int) -> bool:
    return r >= red_min and g <= green_max and b <= blue_max and (r - max(g, b)) >= 30


__all__ = ["TriColorFrame", "prepare_frame"]
