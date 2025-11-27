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
    cached_fix = None
    
    # Performance tracking
    frame_times = []
    last_fps_log = time.time()
    
    # Smart API timing - ISS moves ~7.66 km/s, completes orbit in ~92 min
    # At 72 frames per rotation, each frame is 5° of longitude
    # ISS moves ~5° in about 1.3 minutes, so we can update position less frequently
    api_fetch_interval = 10  # Fetch every 10 seconds (ISS moves ~77km)
    last_api_fetch = 0
    
    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received.")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Initial ISS position fetch
        try:
            cached_fix = iss_client.get_fix()
            last_api_fetch = time.time()
            logger.info(f"Initial ISS Position: Lat {cached_fix.latitude:.2f}, Lon {cached_fix.longitude:.2f}")
        except Exception as e:
            logger.error(f"Failed to get initial ISS position: {e}")
            # Use a default position so display still works
            from dataclasses import dataclass
            @dataclass
            class DummyFix:
                latitude: float = 0.0
                longitude: float = 0.0
            cached_fix = DummyFix()
        
        target_fps = 10
        target_frame_time = 1.0 / target_fps
        
        while running:
            frame_start = time.time()
            
            try:
                # 1. Fetch ISS position (smart timing based on actual frame rate)
                time_since_fetch = time.time() - last_api_fetch
                if time_since_fetch > api_fetch_interval:
                    try:
                        new_fix = iss_client.get_fix()
                        cached_fix = new_fix
                        last_api_fetch = time.time()
                        logger.debug(f"ISS Position: Lat {cached_fix.latitude:.2f}, Lon {cached_fix.longitude:.2f}")
                    except Exception as e:
                        logger.warning(f"API fetch failed, using cached position: {e}")
                
                # 2. Update Display
                driver.update(cached_fix.latitude, cached_fix.longitude)
                
            except Exception as e:
                logger.error(f"Error in update loop: {e}")
            
            # 3. Track frame timing
            frame_time = time.time() - frame_start
            frame_times.append(frame_time)
            
            # Log FPS every 5 seconds
            if time.time() - last_fps_log > 5:
                if frame_times:
                    avg_frame_time = sum(frame_times) / len(frame_times)
                    actual_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
                    logger.info(f"FPS: {actual_fps:.1f} (frame time: {avg_frame_time*1000:.1f}ms)")
                    frame_times = []
                last_fps_log = time.time()
            
            # 4. Wait for next frame
            sleep_time = target_frame_time - frame_time
            if sleep_time > 0.001 and running:
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
