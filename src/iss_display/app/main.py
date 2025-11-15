"""Command line interface for the ISS e-paper display."""

from __future__ import annotations

import argparse
import logging
from typing import Sequence

from PIL import Image

from iss_display.app.scheduler import DisplayScheduler
from iss_display.config import Settings
from iss_display.data.iss_client import ISSClient
from iss_display.data.mapbox_client import MapboxClient
from iss_display.display.epaper_driver import build_driver
from iss_display.display.led import build_led_controller
from iss_display.pipeline.image_preprocessor import FrameEncoder
from iss_display.pipeline.layout import FrameLayout


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def build_scheduler(settings: Settings, *, preview_only: bool | None = None) -> DisplayScheduler:
    iss_client = ISSClient(settings)
    mapbox_client = MapboxClient(settings)
    layout = FrameLayout(
        settings.display_logical_width,
        settings.display_height,
        pin_color=settings.pin_color,
    )
    encoder = FrameEncoder(
        settings.display_width,
        settings.display_height,
        has_red=settings.display_has_red,
        logical_width=settings.display_logical_width,
        pad_left=settings.display_pad_left,
        pad_right=settings.display_pad_right,
    )
    driver = build_driver(
        preview_only=preview_only if preview_only is not None else settings.preview_only,
        preview_dir=settings.preview_dir,
        width=settings.display_width,
        height=settings.display_height,
        has_red=settings.display_has_red,
    )
    led_controller = build_led_controller(settings.led_enabled, settings.led_pin)
    return DisplayScheduler(settings, iss_client, mapbox_client, layout, encoder, driver, led_controller)


def cmd_refresh(settings: Settings, args: argparse.Namespace) -> None:
    scheduler = build_scheduler(settings, preview_only=args.preview_only)
    scheduler.refresh_once(force=args.force_refresh)
    scheduler.close()


def cmd_daemon(settings: Settings, args: argparse.Namespace) -> None:
    scheduler = build_scheduler(settings, preview_only=args.preview_only)
    try:
        scheduler.run_forever()
    finally:
        scheduler.close()


def cmd_cache_only(settings: Settings, args: argparse.Namespace) -> None:
    iss_client = ISSClient(settings)
    fix = iss_client.get_fix(force=True)
    mapbox_client = MapboxClient(settings)
    mapbox_client.get_portrait_image(fix.latitude, fix.longitude, force=args.force_refresh)
    print("Portrait cache refreshed")


def cmd_test_pattern(settings: Settings, args: argparse.Namespace) -> None:
    encoder = FrameEncoder(settings.display_width, settings.display_height, has_red=settings.display_has_red)
    driver = build_driver(
        preview_only=True,
        preview_dir=settings.preview_dir,
        width=settings.display_width,
        height=settings.display_height,
        has_red=settings.display_has_red,
    )
    try:
        canvas = Image.new("RGB", (settings.display_width, settings.display_height), "white")
        red, black = encoder.encode(canvas)
        driver.display_frame(red, black, image=canvas)
    finally:
        driver.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ISS map renderer for e-paper")
    parser.add_argument("--preview-only", action="store_true", help="force preview driver")
    parser.add_argument("--force-refresh", action="store_true", help="ignore caches for the next run")

    sub = parser.add_subparsers(dest="command", required=False)
    sub.add_parser("refresh-once", help="download latest data and push to display")
    sub.add_parser("daemon", help="run scheduler loop forever")
    sub.add_parser("cache-only", help="refresh cached map without writing to display")
    sub.add_parser("test-pattern", help="write a blank frame for hardware checks")

    args = parser.parse_args(argv)
    args.command = args.command or "refresh-once"
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = Settings.load()
    configure_logging(settings.log_level)

    commands = {
        "refresh-once": cmd_refresh,
        "daemon": cmd_daemon,
        "cache-only": cmd_cache_only,
        "test-pattern": cmd_test_pattern,
    }
    command = commands[args.command]
    command(settings, args)


if __name__ == "__main__":  # pragma: no cover - manual execution entry point
    main()
