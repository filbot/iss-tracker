"""Single-run CLI that fetches data, renders a frame, and updates the display."""

from __future__ import annotations

import argparse
import logging
from typing import Sequence

from iss_display.config import Settings
from iss_display.data.geography import get_common_area_name
from iss_display.data.iss_client import ISSClient
from iss_display.data.mapbox_client import MapboxClient
from iss_display.display.epaper_driver import build_driver
from iss_display.pipeline.image_preprocessor import FrameEncoder
from iss_display.pipeline.layout import FrameLayout


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def refresh_once(settings: Settings, *, preview_only: bool) -> None:
    iss_client = ISSClient(settings)
    mapbox_client = MapboxClient(settings)
    layout = FrameLayout(settings.display_logical_width, settings.display_height, pin_color=settings.pin_color)
    encoder = FrameEncoder(
        width=settings.display_width,
        height=settings.display_height,
        has_red=settings.display_has_red,
        logical_width=settings.display_logical_width,
        pad_left=settings.display_pad_left,
        pad_right=settings.display_pad_right,
        rotate_degrees=settings.display_rotation_degrees,
    )
    driver = build_driver(
        preview_only=preview_only,
        preview_dir=settings.preview_dir,
        width=settings.display_width,
        height=settings.display_height,
        has_red=settings.display_has_red,
    )

    try:
        fix = iss_client.get_fix()
        base_map = mapbox_client.get_portrait_image(fix.latitude, fix.longitude)
        location_name = get_common_area_name(fix.latitude, fix.longitude)
        canvas = layout.compose(base_map, fix, location_name)
        red_buffer, black_buffer = encoder.encode(canvas)
        driver.display_frame(red_buffer, black_buffer, image=canvas)
    finally:
        driver.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the ISS e-paper display exactly once")
    parser.add_argument("--preview-only", action="store_true", help="force preview rendering even if hardware is available")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = Settings.load()
    configure_logging(settings.log_level)
    preview_only = args.preview_only or settings.preview_only
    refresh_once(settings, preview_only=preview_only)


if __name__ == "__main__":  # pragma: no cover - script execution only
    main()
