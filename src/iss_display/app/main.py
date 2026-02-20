"""Main application entry point for the ISS Tracker LCD Display."""

from __future__ import annotations

import argparse
import logging
import time
import signal
from collections import deque
from dataclasses import dataclass
from typing import Sequence, Optional
import threading

from iss_display.config import Settings
from iss_display.display.lcd_driver import LcdDisplay
from iss_display.data.iss_client import ISSClient, ISSFix

logger = logging.getLogger(__name__)

# ISS orbital constants
ISS_ORBITAL_PERIOD_SEC = 92.68 * 60  # ~92.68 minutes per orbit

# API backoff limits
_BACKOFF_BASE = 30.0
_BACKOFF_MAX = 300.0


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


class ISSOrbitInterpolator:
    """Interpolates ISS position between API updates using orbital mechanics.

    The ISS follows a predictable ground track due to its 51.6 degree inclination.
    We can accurately predict position for 30-60 seconds between API calls.
    """

    def __init__(self, iss_client: ISSClient, api_interval: float = 30.0):
        self.client = iss_client
        self.api_interval = api_interval

        self._last_fix: Optional[ISSFix] = None
        self._last_fetch_time: float = 0.0

        self._prev_fix: Optional[ISSFix] = None
        self._prev_fetch_time: float = 0.0

        # Estimated velocity (degrees per second)
        self._lon_velocity: float = 360.0 / ISS_ORBITAL_PERIOD_SEC
        self._lat_velocity: float = 0.0

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # API backoff state
        self._consecutive_failures = 0

        # Stats
        self._api_calls = 0
        self._interpolated_frames = 0

    def start(self):
        """Start background API fetching."""
        self._running = True
        self._thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self._thread.start()
        self._do_fetch()
        logger.info(f"ISS Interpolator started (API interval: {self.api_interval}s)")

    def stop(self):
        """Stop background fetching."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info(
            f"ISS Interpolator stopped. "
            f"API calls: {self._api_calls}, "
            f"Interpolated frames: {self._interpolated_frames}"
        )

    def get_telemetry(self) -> ISSFix:
        """Get current interpolated telemetry (thread-safe, non-blocking)."""
        with self._lock:
            if self._last_fix is None:
                return ISSFix(
                    latitude=0.0, longitude=0.0,
                    altitude_km=420.0, velocity_kmh=27600.0,
                    timestamp=time.time()
                )

            now = time.time()
            dt = now - self._last_fetch_time

            new_lon = self._last_fix.longitude + (self._lon_velocity * dt)
            while new_lon > 180:
                new_lon -= 360
            while new_lon < -180:
                new_lon += 360

            new_lat = self._last_fix.latitude + (self._lat_velocity * dt)
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
                if self._last_fix is not None:
                    self._prev_fix = self._last_fix
                    self._prev_fetch_time = self._last_fetch_time

                self._last_fix = fix
                self._last_fetch_time = now
                self._api_calls += 1

                if self._prev_fix is not None and self._prev_fetch_time > 0:
                    dt = now - self._prev_fetch_time
                    if dt > 0.1:
                        dlon = fix.longitude - self._prev_fix.longitude
                        if dlon > 180:
                            dlon -= 360
                        elif dlon < -180:
                            dlon += 360
                        self._lon_velocity = dlon / dt

                        dlat = fix.latitude - self._prev_fix.latitude
                        self._lat_velocity = dlat / dt

                        logger.debug(
                            f"Velocity: lon={self._lon_velocity:.4f} deg/s, "
                            f"lat={self._lat_velocity:.4f} deg/s"
                        )

            self._consecutive_failures = 0
            logger.debug(f"API fetch #{self._api_calls}: Lat {fix.latitude:.2f}, Lon {fix.longitude:.2f}")

        except Exception as e:
            self._consecutive_failures += 1
            logger.warning(f"API fetch failed ({self._consecutive_failures}x): {e}")

    def _fetch_loop(self):
        """Background loop that fetches periodically with exponential backoff."""
        while self._running:
            # Exponential backoff: 30s → 60s → 120s → 300s max
            if self._consecutive_failures > 0:
                backoff = min(
                    _BACKOFF_BASE * (2 ** (self._consecutive_failures - 1)),
                    _BACKOFF_MAX
                )
                logger.debug(f"API backoff: {backoff:.0f}s (failures: {self._consecutive_failures})")
            else:
                backoff = self.api_interval

            time.sleep(backoff)
            if self._running:
                self._do_fetch()


def run_loop(settings: Settings) -> None:
    iss_client = ISSClient(settings)
    driver = LcdDisplay(settings)

    interpolator = ISSOrbitInterpolator(iss_client, api_interval=30.0)
    interpolator.start()

    logger.info("Starting ISS Tracker Display Loop...")

    running = True
    frame_times: deque[float] = deque(maxlen=150)
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

        while running:
            frame_start = time.time()

            try:
                telemetry = interpolator.get_telemetry()
                driver.update_with_telemetry(telemetry)
            except Exception as e:
                logger.error(f"Error in update loop: {e}")

            frame_time = time.time() - frame_start
            frame_times.append(frame_time)

            if time.time() - last_fps_log > 5:
                if frame_times:
                    avg_frame_time = sum(frame_times) / len(frame_times)
                    actual_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
                    logger.info(f"FPS: {actual_fps:.1f} (frame time: {avg_frame_time*1000:.1f}ms)")
                last_fps_log = time.time()

    finally:
        logger.info("Cleaning up...")
        interpolator.stop()
        driver.close()
        logger.info("Done.")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ISS Tracker LCD Display")
    parser.add_argument(
        "--preview-only", action="store_true",
        help="Force preview rendering even if hardware is available"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = Settings.load()

    # Override preview_only from CLI if set
    if args.preview_only and not settings.preview_only:
        # Reconstruct settings with preview_only=True
        settings = Settings(
            iss_api_url=settings.iss_api_url,
            display_width=settings.display_width,
            display_height=settings.display_height,
            preview_dir=settings.preview_dir,
            preview_only=True,
            log_level=settings.log_level,
            gpio_dc=settings.gpio_dc,
            gpio_rst=settings.gpio_rst,
            gpio_bl=settings.gpio_bl,
            spi_bus=settings.spi_bus,
            spi_device=settings.spi_device,
            spi_speed_hz=settings.spi_speed_hz,
        )

    configure_logging(settings.log_level)
    run_loop(settings)


if __name__ == "__main__":
    main()
