#!/usr/bin/env python3
"""Demo script for the Waveshare 2.13" V4 black/white/red panel."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

import epd2in13b_V4
from tri_color_image import TriColorFrame, prepare_frame

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bitmap",
        nargs="?",
        help="Optional bitmap path or filename inside the pic/ folder",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=15.0,
        help="Seconds to keep the rendered frame on-screen (default: 15)",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Skip clearing the panel before drawing the new frame",
    )
    return parser.parse_args()


def _resolve_bitmap(candidate: str) -> Path:
    path = Path(candidate)
    if path.exists():
        return path
    fallback = Path(__file__).with_name("pic") / candidate
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"Cannot find bitmap '{candidate}' or '{fallback}'")


def _load_bitmap(candidate: str) -> Image.Image:
    path = _resolve_bitmap(candidate)
    logging.info("Loaded %s", path)
    with Image.open(path) as image:
        return image.convert("RGB")


def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _generate_demo_art(panel_size: tuple[int, int]) -> Image.Image:
    """Create a self-contained demo canvas showcasing all three colors."""

    width, height = panel_size
    canvas = Image.new("RGB", panel_size, "white")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width - 1, height - 1), outline="black")

    draw.rectangle((6, 6, width - 6, 80), outline="black", width=2)
    draw.rectangle((10, 10, width - 10, 76), fill="#ff4d4d")

    font_large = _load_font(20)
    font_small = _load_font(16)
    draw.text((12, 16), "Waveshare 2.13\"", fill="white", font=font_large)
    draw.text((12, 42), "Tri-color demo", fill="white", font=font_small)

    draw.line((0, height - 40, width, height - 40), fill="black", width=2)
    draw.rectangle((10, height - 35, width - 10, height - 10), outline="red", width=2)
    draw.text((16, height - 32), "Black layer", fill="black", font=font_small)
    draw.text((16, height - 18), "Red overlay", fill="red", font=font_small)
    return canvas


def _build_source_image(epd: epd2in13b_V4.EPD, bitmap: Optional[str]) -> Image.Image:
    if bitmap:
        return _load_bitmap(bitmap)
    logging.info("No bitmap supplied; generating built-in demo artwork")
    return _generate_demo_art((epd.width, epd.height))


def _render_frame(
    epd: epd2in13b_V4.EPD,
    source: Image.Image,
) -> TriColorFrame:
    logging.info("Preparing tri-color frame")
    return prepare_frame(source, epd)


def main() -> None:
    args = _parse_args()
    epd = epd2in13b_V4.EPD()

    try:
        logging.info("Initialising panel")
        if epd.init() != 0:
            raise RuntimeError("Display init failed")
        if not args.no_clear:
            epd.Clear()

        source = _build_source_image(epd, args.bitmap)
        frame = _render_frame(epd, source)
        logging.info("Sending frame to display")
        epd.display(frame.black, frame.red)
        logging.info("Frame displayed; holding for %.1f seconds", args.hold)
        time.sleep(max(args.hold, 0))
    finally:
        logging.info("Putting panel to sleep")
        epd.sleep()


if __name__ == "__main__":
    main()
