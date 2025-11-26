"""Main application entry point for the ISS Tracker LCD Display."""

from __future__ import annotations

import argparse
import logging
import time
import signal
import sys
from typing import Sequence

from iss_display.config import Settings
from iss_display.display.lcd_driver import LcdDisplay
from iss_display.data.iss_client import ISSClient

logger = logging.getLogger(__name__)

def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

def run_loop(settings: Settings, preview_only: bool) -> None:
    iss_client = ISSClient(settings)
    driver = LcdDisplay(settings)
    
    logger.info("Starting ISS Tracker Display Loop...")
    
    running = True
    
    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received.")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        while running:
            start_time = time.time()
            
            try:
                # 1. Fetch Data
                fix = iss_client.get_fix()
                logger.debug(f"ISS Position: Lat {fix.latitude}, Lon {fix.longitude}")
                
                # 2. Update Display
                driver.update(fix.latitude, fix.longitude)
                
            except Exception as e:
                logger.error(f"Error in update loop: {e}")
                # Don't crash the loop on transient errors, just wait and retry
            
            # 3. Wait for next frame
            # We want a reasonable refresh rate. The ISS moves fast (7.66 km/s).
            # But generating the 3D wireframe takes time.
            # Let's aim for updating every 5-10 seconds? Or faster if the Pi can handle it.
            # Let's try 5 seconds for now.
            
            elapsed = time.time() - start_time
            sleep_time = max(1.0, 5.0 - elapsed)
            
            if running:
                time.sleep(sleep_time)
                
    finally:
        logger.info("Cleaning up...")
        driver.close()
        logger.info("Done.")

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ISS Tracker LCD Display")
    parser.add_argument("--preview-only", action="store_true", help="Force preview rendering even if hardware is available")
    return parser.parse_args(argv)

def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = Settings.load()
    configure_logging(settings.log_level)
    
    # Override settings with CLI args if present
    preview_only = args.preview_only or settings.preview_only
    
    run_loop(settings, preview_only=preview_only)

if __name__ == "__main__":
    main()
