"""Main application entry point for the ISS Tracker LCD Display."""

from __future__ import annotations

import argparse
import logging
import time
import signal
import sys
import threading
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


class AsyncISSFetcher:
    """Fetches ISS position in background thread to avoid blocking render loop."""
    
    def __init__(self, iss_client: ISSClient, interval: float = 10.0):
        self.client = iss_client
        self.interval = interval
        self.latitude = 0.0
        self.longitude = 0.0
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
    
    def start(self):
        """Start background fetching."""
        self._running = True
        self._thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self._thread.start()
        # Do initial blocking fetch
        self._do_fetch()
    
    def stop(self):
        """Stop background fetching."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
    
    def get_position(self) -> tuple[float, float]:
        """Get cached position (thread-safe, non-blocking)."""
        with self._lock:
            return self.latitude, self.longitude
    
    def _do_fetch(self):
        """Perform a single fetch."""
        try:
            fix = self.client.get_fix()
            with self._lock:
                self.latitude = fix.latitude
                self.longitude = fix.longitude
            logger.debug(f"ISS Position: Lat {fix.latitude:.2f}, Lon {fix.longitude:.2f}")
        except Exception as e:
            logger.warning(f"API fetch failed: {e}")
    
    def _fetch_loop(self):
        """Background loop that fetches periodically."""
        while self._running:
            time.sleep(self.interval)
            if self._running:
                self._do_fetch()


def run_loop(settings: Settings, preview_only: bool) -> None:
    iss_client = ISSClient(settings)
    driver = LcdDisplay(settings)
    
    # Start async ISS fetcher
    fetcher = AsyncISSFetcher(iss_client, interval=10.0)
    fetcher.start()
    
    logger.info("Starting ISS Tracker Display Loop...")
    
    running = True
    
    # Performance tracking
    frame_times = []
    last_fps_log = time.time()
    
    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received.")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        lat, lon = fetcher.get_position()
        logger.info(f"Initial ISS Position: Lat {lat:.2f}, Lon {lon:.2f}")
        
        # Target max FPS - actual will be limited by SPI transfer time
        target_fps = 30
        target_frame_time = 1.0 / target_fps
        
        while running:
            frame_start = time.time()
            
            try:
                # Get cached position (non-blocking)
                lat, lon = fetcher.get_position()
                
                # Update Display
                driver.update(lat, lon)
                
            except Exception as e:
                logger.error(f"Error in update loop: {e}")
            
            # Track frame timing
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
            
            # Wait for next frame if we're ahead
            sleep_time = target_frame_time - frame_time
            if sleep_time > 0.001 and running:
                time.sleep(sleep_time)
                
    finally:
        logger.info("Cleaning up...")
        fetcher.stop()
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
