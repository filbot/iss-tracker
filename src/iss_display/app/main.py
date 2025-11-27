"""Main application entry point for the ISS Tracker LCD Display."""

from __future__ import annotations

import argparse
import logging
import math
import time
import signal
import sys
import threading
from dataclasses import dataclass
from typing import Sequence, Optional

from iss_display.config import Settings
from iss_display.display.lcd_driver import LcdDisplay
from iss_display.data.iss_client import ISSClient, ISSFix

logger = logging.getLogger(__name__)

# ISS orbital constants
EARTH_RADIUS_KM = 6371.0
ISS_ORBITAL_PERIOD_SEC = 92.68 * 60  # ~92.68 minutes per orbit
ISS_GROUND_SPEED_KM_S = 7.66  # km/s ground speed
ISS_INCLINATION_DEG = 51.6  # orbital inclination


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


class ISSOrbitInterpolator:
    """
    Interpolates ISS position between API updates using orbital mechanics.
    
    The ISS follows a predictable sinusoidal ground track due to its 51.6° inclination.
    We can accurately predict position for 30-60 seconds between API calls.
    """
    
    def __init__(self, iss_client: ISSClient, api_interval: float = 30.0):
        """
        Args:
            iss_client: Client for fetching real position data
            api_interval: Seconds between API calls (default 30s = 2 calls/min)
        """
        self.client = iss_client
        self.api_interval = api_interval
        
        # Last known fix from API
        self._last_fix: Optional[ISSFix] = None
        self._last_fetch_time: float = 0.0
        
        # For velocity estimation between fixes
        self._prev_fix: Optional[ISSFix] = None
        self._prev_fetch_time: float = 0.0
        
        # Estimated velocity (degrees per second)
        self._lon_velocity: float = 360.0 / ISS_ORBITAL_PERIOD_SEC  # ~0.065°/s eastward
        self._lat_velocity: float = 0.0  # Will be estimated from data
        
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # Stats
        self._api_calls = 0
        self._interpolated_frames = 0
    
    def start(self):
        """Start background API fetching."""
        self._running = True
        self._thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self._thread.start()
        # Do initial blocking fetch
        self._do_fetch()
        logger.info(f"ISS Interpolator started (API interval: {self.api_interval}s)")
    
    def stop(self):
        """Stop background fetching."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info(f"ISS Interpolator stopped. API calls: {self._api_calls}, Interpolated frames: {self._interpolated_frames}")
    
    def get_telemetry(self) -> ISSFix:
        """
        Get current interpolated telemetry (thread-safe, non-blocking).
        
        Returns the last API fix with lat/lon interpolated to current time.
        """
        with self._lock:
            if self._last_fix is None:
                # No data yet, return default
                return ISSFix(
                    latitude=0.0, longitude=0.0,
                    altitude_km=420.0, velocity_kmh=27600.0,
                    timestamp=time.time()
                )
            
            # Calculate time since last fetch
            now = time.time()
            dt = now - self._last_fetch_time
            
            # Interpolate position
            # Longitude: ISS moves eastward ~0.065°/s (completes 360° in ~92 min)
            # Latitude: oscillates between ±51.6° in sinusoidal pattern
            
            new_lon = self._last_fix.longitude + (self._lon_velocity * dt)
            # Wrap longitude to -180 to 180
            while new_lon > 180:
                new_lon -= 360
            while new_lon < -180:
                new_lon += 360
            
            # Latitude interpolation (simple linear for short intervals)
            new_lat = self._last_fix.latitude + (self._lat_velocity * dt)
            # Clamp latitude to valid range
            new_lat = max(-90, min(90, new_lat))
            
            self._interpolated_frames += 1
            
            return ISSFix(
                latitude=new_lat,
                longitude=new_lon,
                altitude_km=self._last_fix.altitude_km,
                velocity_kmh=self._last_fix.velocity_kmh,
                timestamp=now
            )
    
    def _do_fetch(self):
        """Perform a single API fetch and update velocity estimates."""
        try:
            fix = self.client.get_fix()
            now = time.time()
            
            with self._lock:
                # Store previous fix for velocity calculation
                if self._last_fix is not None:
                    self._prev_fix = self._last_fix
                    self._prev_fetch_time = self._last_fetch_time
                
                self._last_fix = fix
                self._last_fetch_time = now
                self._api_calls += 1
                
                # Estimate velocities from consecutive fixes
                if self._prev_fix is not None and self._prev_fetch_time > 0:
                    dt = now - self._prev_fetch_time
                    if dt > 0.1:  # Avoid division by tiny numbers
                        # Longitude velocity
                        dlon = fix.longitude - self._prev_fix.longitude
                        # Handle wraparound at ±180°
                        if dlon > 180:
                            dlon -= 360
                        elif dlon < -180:
                            dlon += 360
                        self._lon_velocity = dlon / dt
                        
                        # Latitude velocity
                        dlat = fix.latitude - self._prev_fix.latitude
                        self._lat_velocity = dlat / dt
                        
                        logger.debug(f"Velocity: lon={self._lon_velocity:.4f}°/s, lat={self._lat_velocity:.4f}°/s")
            
            logger.debug(f"API fetch #{self._api_calls}: Lat {fix.latitude:.2f}, Lon {fix.longitude:.2f}")
            
        except Exception as e:
            logger.warning(f"API fetch failed: {e}")
    
    def _fetch_loop(self):
        """Background loop that fetches periodically."""
        while self._running:
            time.sleep(self.api_interval)
            if self._running:
                self._do_fetch()


def run_loop(settings: Settings, preview_only: bool) -> None:
    iss_client = ISSClient(settings)
    driver = LcdDisplay(settings)
    
    # Start ISS interpolator with 30-second API interval
    # This means ~2 API calls per minute, ~120 per hour (well under any rate limits)
    interpolator = ISSOrbitInterpolator(iss_client, api_interval=30.0)
    interpolator.start()
    
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
        telemetry = interpolator.get_telemetry()
        logger.info(f"Initial ISS Position: Lat {telemetry.latitude:.2f}, Lon {telemetry.longitude:.2f}")
        
        # Target max FPS - actual will be limited by SPI transfer time
        target_fps = 30
        target_frame_time = 1.0 / target_fps
        
        while running:
            frame_start = time.time()
            
            try:
                # Get interpolated telemetry (non-blocking, always fresh)
                telemetry = interpolator.get_telemetry()
                
                # Update Display with full telemetry
                driver.update_with_telemetry(telemetry)
                
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
        interpolator.stop()
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
