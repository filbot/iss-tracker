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
    last_api_fetch = 0
    api_fetch_interval = 10  # Fetch ISS position every 10 seconds
    cached_fix = None
    
    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received.")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Show initial display immediately with default position
    logger.info("Displaying initial frame...")
    try:
        driver.update(0.0, 0.0)
        logger.info("Initial frame displayed")
    except Exception as e:
        logger.error(f"Failed to display initial frame: {e}")

    try:
        while running:
            start_time = time.time()
            
            try:
                # 1. Fetch Data (only every api_fetch_interval seconds)
                if time.time() - last_api_fetch > api_fetch_interval or cached_fix is None:
                    cached_fix = iss_client.get_fix()
                    last_api_fetch = time.time()
                    logger.debug(f"ISS Position: Lat {cached_fix.latitude}, Lon {cached_fix.longitude}")
                
                fix = cached_fix
                
                # 2. Update Display
                driver.update(fix.latitude, fix.longitude)
                
            except Exception as e:
                logger.error(f"Error in update loop: {e}")
                # Don't crash the loop on transient errors, just wait and retry
            
            # 3. Wait for next frame
            # For smooth rotation animation, update frequently
            # ISS position is cached and only fetched every few seconds
            
            elapsed = time.time() - start_time
            sleep_time = max(0.1, 0.5 - elapsed)  # Target ~2 FPS for smooth rotation
            
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
