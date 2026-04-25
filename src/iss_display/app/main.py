"""Main application entry point for the ISS Tracker LCD Display."""

from __future__ import annotations

import argparse
import gc
import logging
import os
import socket
import sys
import time
import signal
from typing import Sequence, Optional
import threading

from iss_display.config import Settings
from iss_display.display.lcd_driver import LcdDisplay
from iss_display.data.iss_client import ISSClient, ISSFetchError, ISSFix
from iss_display.data.astros_client import AstrosClient

try:
    import RPi.GPIO as GPIO
    _HW_AVAILABLE = True
except ImportError:
    _HW_AVAILABLE = False

logger = logging.getLogger(__name__)

# ISS orbital constants
ISS_ORBITAL_PERIOD_SEC = 92.68 * 60  # ~92.68 minutes per orbit

# API backoff limits
_BACKOFF_BASE = 30.0
_BACKOFF_MAX = 60.0

# Error recovery thresholds
_REINIT_AFTER_ERRORS = 5
_EXIT_AFTER_ERRORS = 20

# Thread health: consider stale after 2 minutes without a successful fetch
_THREAD_STALE_SEC = 120.0

# Data staleness: if no successful fetch in this many seconds, force restart
_MAX_DATA_AGE_SEC = 600.0

# Render thread: consider stuck if no heartbeat for this long
_RENDER_STALE_SEC = 10.0

# Periodic GC: collect cyclic references from requests/SSL without
# leaving GC enabled (which would trigger unpredictable collections
# during render-critical sections).
_GC_INTERVAL_SEC = 1800.0  # 30 minutes


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _sd_notify(message: str) -> None:
    """Send a notification to systemd (if running under systemd).

    Uses raw socket to $NOTIFY_SOCKET, avoiding python-systemd dependency.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        if addr[0] == "@":
            addr = "\0" + addr[1:]
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.sendto(message.encode(), addr)
        finally:
            sock.close()
    except Exception:
        pass


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

        # Thread health: monotonic timestamp of last loop iteration
        self._thread_heartbeat: float = 0.0

    def start(self):
        """Start background API fetching."""
        self._running = True
        self._thread_heartbeat = time.monotonic()
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

    def is_healthy(self) -> bool:
        """Check if the fetch thread is alive, responsive, and delivering data."""
        if self._thread is None or not self._thread.is_alive():
            logger.debug("Health check: thread dead")
            return False
        if time.monotonic() - self._thread_heartbeat > _THREAD_STALE_SEC:
            logger.debug("Health check: heartbeat stale (%.0fs)",
                         time.monotonic() - self._thread_heartbeat)
            return False
        if self._last_fetch_time > 0 and time.time() - self._last_fetch_time > _MAX_DATA_AGE_SEC:
            logger.debug("Health check: data stale (%.0fs)",
                         time.time() - self._last_fetch_time)
            return False
        return True

    def restart_if_needed(self) -> bool:
        """Restart the fetch thread if it has died. Returns True if restarted."""
        if self.is_healthy():
            return False
        logger.warning(
            "Fetch thread unhealthy, restarting (failures=%d, data_age=%.0fs)",
            self._consecutive_failures,
            time.time() - self._last_fetch_time if self._last_fetch_time > 0 else -1,
        )
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._consecutive_failures = 0
        self.client.reset_session()
        self.start()
        return True

    def get_telemetry(self) -> ISSFix:
        """Get current interpolated telemetry (thread-safe, non-blocking)."""
        with self._lock:
            if self._last_fix is None:
                # Fall back to the client's cached position from a previous
                # session if the very first fetch failed.
                cached = self.client._last_fix
                if cached is not None:
                    return cached
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
                timestamp=now,
                data_age_sec=dt,
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
            self._thread_heartbeat = time.monotonic()
            logger.debug(f"API fetch #{self._api_calls}: Lat {fix.latitude:.2f}, Lon {fix.longitude:.2f}")

        except ISSFetchError as e:
            self._consecutive_failures += 1
            # Do NOT update _last_fix or _last_fetch_time — preserving the old
            # timestamp lets data_age_sec in get_telemetry() grow correctly.
            if self.client._last_fix is not None:
                logger.warning(
                    f"All APIs failed ({self._consecutive_failures}x), using last known position. "
                    f"Errors: {e}"
                )
            else:
                logger.warning(
                    f"All APIs failed ({self._consecutive_failures}x), no cached position available. "
                    f"Errors: {e}"
                )

        except Exception:
            self._consecutive_failures += 1
            logger.exception("API fetch failed (%dx)", self._consecutive_failures)

    def _fetch_loop(self):
        """Background loop that fetches periodically with exponential backoff."""
        while self._running:
            try:
                self._thread_heartbeat = time.monotonic()

                # Exponential backoff: 30s → 60s max
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
            except Exception:
                logger.exception("Unexpected error in fetch loop")
                time.sleep(5.0)


class ViewToggle:
    """Polls a GPIO toggle switch to determine the active display view.

    ON (LOW / closed to GND) = ISS tracker view
    OFF (HIGH / open, pulled up) = Crew view

    In preview mode (no GPIO), defaults to ISS view.
    """

    ISS_VIEW = 0
    CREW_VIEW = 1

    def __init__(self, gpio_pin: int, preview_mode: bool):
        self._pin = gpio_pin
        self._preview = preview_mode
        self._current_view = self.ISS_VIEW
        self._prev_view = self.ISS_VIEW

        if not preview_mode and _HW_AVAILABLE:
            # GPIO.setmode already called by ST7796S._init_gpio() before us
            GPIO.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            logger.info("Toggle switch initialized on GPIO %d", self._pin)
        else:
            logger.info("Toggle switch: preview mode, defaulting to ISS view")

    def poll(self) -> int:
        """Read the current switch position. Call from main loop (~100ms)."""
        self._prev_view = self._current_view

        if self._preview or not _HW_AVAILABLE:
            return self._current_view

        try:
            pin_state = GPIO.input(self._pin)
            # LOW (0) = switch closed to GND = ISS view
            # HIGH (1) = switch open, pulled up = Crew view
            self._current_view = self.CREW_VIEW if pin_state else self.ISS_VIEW
        except Exception as e:
            logger.warning("Toggle switch read failed: %s", e)

        return self._current_view

    def view_changed(self) -> bool:
        """True if the view changed on the last poll()."""
        return self._current_view != self._prev_view


class WatchdogPinger(threading.Thread):
    """Sends systemd watchdog pings on a fixed cadence, conditioned on
    main-loop and renderer health.

    Pinging from a dedicated thread decouples watchdog liveness from any
    transient stall in the main loop. The thread refuses to ping when the
    main loop or renderer thread is stale — that way systemd can recover
    a stuck process even if some thread is still alive enough to send pings.
    """

    PING_INTERVAL_SEC = 10.0
    STALE_THRESHOLD_SEC = 30.0

    def __init__(self, get_status):
        super().__init__(daemon=True, name="sd-watchdog")
        self._stop = threading.Event()
        # callable returning (main_loop_age_sec, render_age_sec)
        self._get_status = get_status

    def run(self) -> None:
        while not self._stop.wait(self.PING_INTERVAL_SEC):
            try:
                main_age, render_age = self._get_status()
            except Exception:
                logger.exception("Watchdog status callback failed")
                continue
            if main_age < self.STALE_THRESHOLD_SEC and render_age < self.STALE_THRESHOLD_SEC:
                _sd_notify("WATCHDOG=1")
            else:
                logger.critical(
                    "Skipping watchdog ping: main_age=%.1fs render_age=%.1fs",
                    main_age, render_age,
                )

    def stop(self) -> None:
        self._stop.set()


class DisplayRenderer(threading.Thread):
    """Dedicated thread for smooth globe rotation rendering.

    Isolates all display I/O (SPI writes, GPIO) from the main thread so that
    housekeeping work (GC, watchdog, telemetry fetching) cannot delay frame
    delivery.  Uses globe-frame-aligned sleep to wake up within ~2 ms of each
    frame transition rather than a fixed-interval poll.
    """

    def __init__(self, lcd_display: LcdDisplay):
        super().__init__(daemon=True, name="display-renderer")
        self._lcd = lcd_display
        self._running = True
        self._lock = threading.Lock()
        self._telemetry: Optional[ISSFix] = None
        self.heartbeat: float = time.monotonic()
        self._consecutive_errors = 0
        self._active_view: int = ViewToggle.ISS_VIEW
        self._crew_data = None
        self._crew_rendered = False

    def set_telemetry(self, telemetry: ISSFix):
        """Update the telemetry snapshot read by the render loop."""
        with self._lock:
            self._telemetry = telemetry

    def set_view(self, view: int):
        """Set the active view (called from main thread).

        Owns the full-frame resync on transitions back to ISS view, so the
        renderer is self-consistent and the main loop doesn't need to know
        about LcdDisplay internals.
        """
        with self._lock:
            if view != self._active_view:
                self._active_view = view
                self._crew_rendered = False
                if view == ViewToggle.CREW_VIEW:
                    self._lcd.invalidate_crew_cache()
                else:
                    # Switching back to ISS view: force a full frame to
                    # resync the display with the globe buffer (the crew
                    # view clobbered _frame_buf with text pixels).
                    self._lcd.force_full_frame()

    def set_crew_data(self, data):
        """Update the crew data snapshot for the crew view."""
        with self._lock:
            if data is not self._crew_data:
                self._crew_data = data
                self._crew_rendered = False

    def stop(self):
        self._running = False

    def run(self):
        lcd = self._lcd
        frame_period = lcd._rotation_period / lcd.num_frames

        while self._running:
            try:
                with self._lock:
                    active_view = self._active_view

                if active_view == ViewToggle.ISS_VIEW:
                    self._run_iss_frame(lcd, frame_period)
                else:
                    self._run_crew_frame(lcd)
            except (SystemExit, KeyboardInterrupt):
                raise
            except Exception:
                self._consecutive_errors += 1
                logger.exception(
                    "Unhandled error in render loop (%dx)", self._consecutive_errors
                )
                self._handle_render_error(lcd)
            finally:
                # Heartbeat must update on every iteration, including error paths,
                # or the main loop's stale-render detector will fire spuriously
                # during long error loops.
                self.heartbeat = time.monotonic()

    def _run_iss_frame(self, lcd, frame_period):
        """Render one ISS globe frame with precise timing."""
        # Wait for the next globe frame boundary using hybrid sleep:
        # coarse time.sleep() for most of the wait, then busy-wait
        # for the final ms to get sub-ms precision on the Pi 3.
        now = time.time()
        elapsed = now - lcd._rotation_start_time
        time_in_frame = elapsed % frame_period
        time_to_next = frame_period - time_in_frame

        if time_to_next > 0.008:
            time.sleep(time_to_next - 0.006)

        target = now + time_to_next
        while time.time() < target:
            pass  # busy-wait for precise frame alignment

        # Read latest telemetry (brief lock)
        with self._lock:
            telemetry = self._telemetry

        if telemetry is not None:
            try:
                lcd.update_with_telemetry(telemetry)
                self._consecutive_errors = 0
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Render error ({self._consecutive_errors}x): {e}")
                self._handle_render_error(lcd)

    def _run_crew_frame(self, lcd):
        """Render the crew view (static, re-render only on data change)."""
        with self._lock:
            crew_data = self._crew_data
            need_render = (not self._crew_rendered) and (crew_data is not None)

        if need_render:
            try:
                lcd.render_crew_view(crew_data)
                with self._lock:
                    # Identity check: only mark rendered if no invalidation
                    # (set_view / set_crew_data) fired during the render.
                    # Otherwise we'd clobber the invalidation and show stale
                    # data until the next view toggle.
                    if self._crew_data is crew_data:
                        self._crew_rendered = True
                self._consecutive_errors = 0
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Crew render error ({self._consecutive_errors}x): {e}")
                self._handle_render_error(lcd)

        # No animation — sleep, wake to check for view/data change
        time.sleep(0.1)

    def _handle_render_error(self, lcd):
        """Handle consecutive render errors with escalating recovery."""
        if self._consecutive_errors >= _EXIT_AFTER_ERRORS:
            logger.critical(
                f"{self._consecutive_errors} consecutive render errors, "
                f"signalling main loop to exit for systemd restart"
            )
            # sys.exit() inside a thread only raises SystemExit in this thread;
            # it does not exit the process. Set the running flag so the main
            # loop's renderer.is_alive() check exits cleanly via sys.exit(1).
            self._running = False
            return
        if self._consecutive_errors >= _REINIT_AFTER_ERRORS:
            logger.warning(
                f"{self._consecutive_errors} consecutive errors, "
                f"attempting display re-init"
            )
            try:
                lcd.reinit()
            except Exception as reinit_err:
                logger.error(f"Display re-init failed: {reinit_err}")


def run_loop(settings: Settings) -> None:
    iss_client = ISSClient(settings)
    astros_client = AstrosClient()
    driver = LcdDisplay(settings)

    interpolator = ISSOrbitInterpolator(iss_client, api_interval=30.0)
    interpolator.start()

    # Astros client runs its own refresh thread; start() does one
    # synchronous fetch then spawns the background loop.
    astros_client.start()

    logger.info("Starting ISS Tracker Display Loop...")

    running = True
    last_thread_check = time.monotonic()

    renderer = DisplayRenderer(driver)

    # Toggle switch: GPIO input, or preview mode fallback
    preview_mode = settings.preview_only or not _HW_AVAILABLE
    toggle = ViewToggle(settings.gpio_toggle, preview_mode)

    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received.")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Disable automatic GC to prevent unpredictable stop-the-world pauses
    # that block ALL threads (including the render thread) via the GIL.
    # With pre-allocated arrays, reference counting handles most cleanup.
    gc.disable()
    gc.collect()  # one clean sweep before starting
    logger.info("Automatic GC disabled")
    last_gc_time = time.monotonic()

    try:
        telemetry = interpolator.get_telemetry()
        logger.info(f"Initial ISS Position: Lat {telemetry.latitude:.2f}, Lon {telemetry.longitude:.2f}")
        renderer.set_telemetry(telemetry)

        # Read toggle state BEFORE starting renderer to avoid ISS view flash
        initial_view = toggle.poll()
        if initial_view == ViewToggle.CREW_VIEW:
            renderer.set_view(ViewToggle.CREW_VIEW)
            crew_data = astros_client.get_astros()
            renderer.set_crew_data(crew_data)
            logger.info("Starting in CREW view (toggle switch off)")

        renderer.start()

        # Start watchdog pinger thread. It pings every 10s only if both the
        # main loop and render thread are fresh — so a hung main loop or a
        # silently-dead renderer will let systemd time us out and restart.
        last_main_tick = time.monotonic()

        def _watchdog_status():
            now = time.monotonic()
            return (now - last_main_tick, now - renderer.heartbeat)

        watchdog = WatchdogPinger(_watchdog_status)
        watchdog.start()

        _sd_notify("READY=1")

        while running:
            last_main_tick = time.monotonic()

            # Poll toggle switch
            current_view = toggle.poll()

            if toggle.view_changed():
                view_name = "ISS" if current_view == ViewToggle.ISS_VIEW else "CREW"
                logger.info("View switched to %s", view_name)
                renderer.set_view(current_view)

            # Always keep ISS telemetry flowing (even in crew view)
            telemetry = interpolator.get_telemetry()
            renderer.set_telemetry(telemetry)

            # Feed crew data to renderer when in crew view. get_astros() is a
            # non-blocking cache read; the background fetcher refreshes it.
            if current_view == ViewToggle.CREW_VIEW:
                crew_data = astros_client.get_astros()
                if crew_data is not None:
                    renderer.set_crew_data(crew_data)

            now_mono = time.monotonic()

            # Check fetch thread health every 30 seconds
            if now_mono - last_thread_check > 30.0:
                last_thread_check = now_mono
                interpolator.restart_if_needed()

                # Check render thread health
                if not renderer.is_alive():
                    logger.critical("Render thread died, exiting for systemd restart")
                    sys.exit(1)
                if now_mono - renderer.heartbeat > _RENDER_STALE_SEC:
                    logger.critical("Render thread stuck, exiting for systemd restart")
                    sys.exit(1)

            # Periodic GC: collect cyclic references leaked by requests/SSL.
            # Measured: ~7k objects per 30-min interval. Auto-GC stays disabled
            # to avoid per-allocation pauses on the render path; collection is
            # batched here. The pause is GIL-wide regardless of which thread
            # initiates it, so moving it off the main loop would not reduce
            # the stop-the-world duration.
            if now_mono - last_gc_time > _GC_INTERVAL_SEC:
                last_gc_time = now_mono
                collected = gc.collect()
                if collected > 0:
                    logger.info("GC collected %d objects", collected)

            time.sleep(0.100)

    finally:
        gc.enable()
        logger.info("Cleaning up...")
        _sd_notify("STOPPING=1")
        try:
            watchdog.stop()
        except NameError:
            pass  # never started (failed before line that assigns watchdog)
        renderer.stop()
        renderer.join(timeout=2.0)
        interpolator.stop()
        astros_client.stop()
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
            n2yo_api_key=settings.n2yo_api_key,
            display_width=settings.display_width,
            display_height=settings.display_height,
            preview_dir=settings.preview_dir,
            preview_only=True,
            log_level=settings.log_level,
            gpio_dc=settings.gpio_dc,
            gpio_rst=settings.gpio_rst,
            gpio_bl=settings.gpio_bl,
            gpio_toggle=settings.gpio_toggle,
            spi_bus=settings.spi_bus,
            spi_device=settings.spi_device,
            spi_speed_hz=settings.spi_speed_hz,
        )

    configure_logging(settings.log_level)
    run_loop(settings)


if __name__ == "__main__":
    main()
