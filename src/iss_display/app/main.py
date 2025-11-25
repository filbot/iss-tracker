"""Single-run CLI that fetches data, renders a frame, and updates the display."""

from __future__ import annotations

import argparse
import logging
from typing import Sequence

from iss_display.config import Settings
# from iss_display.data.geography import get_common_area_name
# from iss_display.data.iss_client import ISSClient # Imported inside function
# from iss_display.data.mapbox_client import MapboxClient
# from iss_display.display.epaper_driver import build_driver
# from iss_display.pipeline.image_preprocessor import FrameEncoder
# from iss_display.pipeline.layout import FrameLayout


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def refresh_once(settings: Settings, *, preview_only: bool) -> None:
    # New LCD Driver
    from iss_display.display.lcd_driver import LcdDisplay
    from iss_display.data.iss_client import ISSClient
    
    iss_client = ISSClient(settings)
    driver = LcdDisplay(settings)
    
    # We no longer need mapbox or complex layout for the wireframe UI
    # Just get the fix and update the display
    
    try:
        fix = iss_client.get_fix()
        driver.update(fix.latitude, fix.longitude)
    except Exception:
        logging.exception("Failed to update display")
        raise
    finally:
        # driver.close() # LcdDisplay doesn't have close yet, but good practice
        pass


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
