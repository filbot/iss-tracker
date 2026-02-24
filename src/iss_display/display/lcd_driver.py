import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Union, TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont
import numpy as np

if TYPE_CHECKING:
    from iss_display.data.iss_client import ISSFix

try:
    import spidev
    import RPi.GPIO as GPIO
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False

from iss_display.config import Settings
from iss_display.data.geography import get_common_area_name
from iss_display.theme import THEME, rgb_to_hex, resolve_text_style, resolve_border_color

logger = logging.getLogger(__name__)

# ST7796S Command Constants
SWRESET = 0x01
SLPIN   = 0x10
SLPOUT  = 0x11
NORON   = 0x13
INVON   = 0x21
DISPOFF = 0x28
DISPON  = 0x29
CASET   = 0x2A
RASET   = 0x2B
RAMWR   = 0x2C
MADCTL  = 0x36
COLMOD  = 0x3A
RDDST   = 0x09

# Recovery constants
_MAX_RECOVERY_ATTEMPTS = 3
_LIGHT_REINIT_INTERVAL_SEC = 15 * 60    # 15 minutes
_FULL_REINIT_INTERVAL_SEC = 60 * 60     # 60 minutes
_HEALTH_CHECK_INTERVAL_SEC = 60         # 1 minute

RGB = Tuple[int, int, int]


@dataclass
class _ResolvedText:
    """Fully resolved text style with loaded PIL font."""
    color: RGB
    font: ImageFont.FreeTypeFont


@dataclass
class _ResolvedElement:
    """Pre-resolved rendering parameters for one HUD element."""
    label: _ResolvedText
    value: _ResolvedText
    unit: Optional[_ResolvedText]          # None for elements with no unit (OVER)
    cell_width: Optional[int]              # None = right-aligned element
    unit_baseline_offset: int = 0          # offset to align unit baseline with value


def _rgb_to_rgb565(r: int, g: int, b: int) -> int:
    """Convert an RGB color to a 16-bit RGB565 value (big-endian byte order)."""
    val = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return val


class ST7796S:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.width = settings.display_width
        self.height = settings.display_height

        self.dc = settings.gpio_dc
        self.rst = settings.gpio_rst
        self.bl = settings.gpio_bl

        self._consecutive_failures = 0
        self._last_light_reinit = time.monotonic()
        self._last_full_reinit = time.monotonic()
        self._last_health_check = time.monotonic()
        self._health_check_supported = True  # disabled if readback returns all zeros
        self._health_check_zero_count = 0

        self._init_gpio()
        self._init_spi()
        self._init_display(first_boot=True)

    def _init_gpio(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.dc, GPIO.OUT)
        GPIO.setup(self.rst, GPIO.OUT)
        GPIO.setup(self.bl, GPIO.OUT)
        GPIO.output(self.bl, GPIO.LOW)

    def _init_spi(self):
        self.spi = spidev.SpiDev()
        self.spi.open(self.settings.spi_bus, self.settings.spi_device)
        self.spi.max_speed_hz = self.settings.spi_speed_hz
        self.spi.mode = 0b00
        logger.info(f"SPI initialized: bus={self.settings.spi_bus}, "
                     f"device={self.settings.spi_device}, "
                     f"speed={self.settings.spi_speed_hz / 1_000_000:.1f} MHz")

    def _reset(self):
        GPIO.output(self.rst, GPIO.HIGH)
        time.sleep(0.02)
        GPIO.output(self.rst, GPIO.LOW)
        time.sleep(0.02)
        GPIO.output(self.rst, GPIO.HIGH)
        time.sleep(0.20)

    def command(self, cmd: int):
        GPIO.output(self.dc, GPIO.LOW)
        self.spi.writebytes([cmd])

    def data(self, data: int):
        GPIO.output(self.dc, GPIO.HIGH)
        self.spi.writebytes([data])

    def _init_display(self, *, first_boot: bool = False):
        self._reset()

        self.command(SWRESET)
        time.sleep(0.15)

        self.command(SLPOUT)
        time.sleep(0.15)

        self.command(COLMOD)
        self.data(0x55)  # 16-bit/pixel

        # Memory Access Control: MX=1, BGR=1
        self.command(MADCTL)
        self.data(0x48)

        self.command(INVON)

        self.command(NORON)
        time.sleep(0.01)

        self.command(DISPON)
        time.sleep(0.01)

        self.set_window(0, 0, self.width - 1, self.height - 1)

        if first_boot:
            GPIO.output(self.bl, GPIO.HIGH)
            self._fill(0x0000)

        now = time.monotonic()
        self._last_light_reinit = now
        self._last_full_reinit = now
        self._last_health_check = now
        self._consecutive_failures = 0
        logger.info("Display initialized")

    def _light_reinit(self):
        """Reaffirm critical controller registers without sleep/wake transitions.

        Only sends stateless register-write commands that correct potential
        drift without triggering display blanking. SLPOUT, NORON, and DISPON
        are intentionally omitted — they are no-ops on an already-awake display
        but can cause brief visual artifacts on some ST7796S modules.
        """
        try:
            self.command(COLMOD)
            self.data(0x55)
            self.command(MADCTL)
            self.data(0x48)
            self.command(INVON)
            self.set_window(0, 0, self.width - 1, self.height - 1)
            self._last_light_reinit = time.monotonic()
            logger.info("Display light re-init complete")
        except Exception as e:
            logger.warning(f"Light re-init failed: {e}")
            self._recover()

    def _full_reinit(self):
        """Full re-init with hardware reset — recovers from any controller state."""
        try:
            self._init_display()
            self._last_full_reinit = time.monotonic()
            logger.info("Display full re-init complete")
        except Exception as e:
            logger.error(f"Full re-init failed: {e}")
            self._recover()

    def _check_health(self) -> bool:
        """Read display status register to verify controller state.

        Returns True if healthy, False if re-init is needed.
        Some LCD modules don't support SPI readback (MISO not functional or
        protocol incompatible). If we get all-zero responses 3 times in a row,
        we disable readback and rely solely on periodic re-init.
        """
        if not self._health_check_supported:
            self._last_health_check = time.monotonic()
            return True

        try:
            self.command(RDDST)
            GPIO.output(self.dc, GPIO.HIGH)
            # Read 5 bytes: 1 dummy + 4 status bytes
            status = self.spi.xfer2([0x00] * 5)
            self._last_health_check = time.monotonic()

            # Detect non-functional readback: all zeros means the module
            # doesn't support SPI reads (common on many cheap SPI LCD boards)
            if all(b == 0 for b in status):
                self._health_check_zero_count += 1
                if self._health_check_zero_count >= 3:
                    logger.debug("Display status readback returns all zeros — "
                                 "disabling RDDST health checks, relying on periodic re-init")
                    self._health_check_supported = False
                return True  # assume healthy since display was working

            # Got real data — reset zero counter
            self._health_check_zero_count = 0

            # Status byte 1 (index 1): bit 2 = display on, bit 4 = normal mode
            st1 = status[1]
            display_on = bool(st1 & 0x04)
            normal_mode = bool(st1 & 0x10)

            if not display_on or not normal_mode:
                logger.warning(f"Display health check failed: status={[hex(b) for b in status]}, "
                               f"display_on={display_on}, normal_mode={normal_mode}")
                return False

            logger.debug(f"Display health OK: status={[hex(b) for b in status]}")
            return True
        except Exception as e:
            logger.warning(f"Display health check read failed: {e}")
            return False

    def _periodic_maintenance(self):
        """Run periodic health checks and re-initialization."""
        now = time.monotonic()

        # Health check every 60 seconds
        if now - self._last_health_check >= _HEALTH_CHECK_INTERVAL_SEC:
            if not self._check_health():
                logger.warning("Health check triggered re-init")
                self._full_reinit()
                return

        # Full re-init every 60 minutes
        if now - self._last_full_reinit >= _FULL_REINIT_INTERVAL_SEC:
            self._full_reinit()
            return

        # Light re-init every 15 minutes
        if now - self._last_light_reinit >= _LIGHT_REINIT_INTERVAL_SEC:
            self._light_reinit()

    def _recover(self):
        """Attempt to recover SPI bus and display from a failed state."""
        logger.warning(f"Attempting SPI/display recovery (failures: {self._consecutive_failures})...")
        try:
            self.spi.close()
        except Exception:
            pass

        time.sleep(0.1)

        try:
            self._init_spi()
            if self._consecutive_failures >= _MAX_RECOVERY_ATTEMPTS:
                logger.warning("Multiple failures, performing hardware reset")
                self._reset()
                time.sleep(0.2)
            self._init_display()
            logger.info("SPI/display recovery successful")
        except Exception as e:
            logger.error(f"Recovery failed: {e}")

    def _fill(self, color: int):
        """Fill the entire screen with a solid color (RGB565)."""
        self.set_window(0, 0, self.width - 1, self.height - 1)
        high = (color >> 8) & 0xFF
        low = color & 0xFF
        pixel_data = bytes([high, low] * (self.width * self.height))
        GPIO.output(self.dc, GPIO.HIGH)
        self.spi.writebytes2(pixel_data)

    def set_window(self, x0, y0, x1, y1):
        self.command(CASET)
        self.data(x0 >> 8)
        self.data(x0 & 0xFF)
        self.data(x1 >> 8)
        self.data(x1 & 0xFF)

        self.command(RASET)
        self.data(y0 >> 8)
        self.data(y0 & 0xFF)
        self.data(y1 >> 8)
        self.data(y1 & 0xFF)

        self.command(RAMWR)

    def display_raw(self, pixel_bytes: Union[bytes, bytearray]):
        """Display pre-converted RGB565 data directly, with error recovery."""
        try:
            self.set_window(0, 0, self.width - 1, self.height - 1)
            GPIO.output(self.dc, GPIO.HIGH)
            self.spi.writebytes2(pixel_bytes)
            self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            logger.error(f"SPI write failed ({self._consecutive_failures}x): {e}")
            self._recover()

    def maybe_run_maintenance(self):
        """Run periodic maintenance if any interval has elapsed.

        Call this between frames from the main loop, NOT during frame writes.
        """
        self._periodic_maintenance()

    def close(self):
        """Properly shut down the display with robust error handling."""
        # Backlight off first — immediate visual feedback
        try:
            GPIO.output(self.bl, GPIO.LOW)
        except Exception:
            pass

        # Clear screen (single fill, IPS panels don't ghost)
        try:
            black_screen = bytes(self.width * self.height * 2)
            self.set_window(0, 0, self.width - 1, self.height - 1)
            self.command(RAMWR)
            GPIO.output(self.dc, GPIO.HIGH)
            self.spi.writebytes2(black_screen)
            time.sleep(0.05)
        except Exception as e:
            logger.debug(f"Screen clear during shutdown failed: {e}")

        # Display off command
        try:
            self.command(DISPOFF)
            time.sleep(0.05)
        except Exception:
            pass

        # Sleep mode
        try:
            self.command(SLPIN)
            time.sleep(0.12)
        except Exception:
            pass

        # Hardware reset — guarantees known state for next startup
        try:
            GPIO.output(self.rst, GPIO.LOW)
            time.sleep(0.05)
        except Exception:
            pass

        # Release hardware resources
        try:
            self.spi.close()
        except Exception:
            pass

        try:
            GPIO.cleanup()
        except Exception:
            pass

        logger.info("Display shut down cleanly")


class LcdDisplay:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.width = settings.display_width
        self.height = settings.display_height
        self._bytes_per_row = self.width * 2  # 2 bytes per pixel (RGB565)

        self.driver: Optional[ST7796S] = None
        if not settings.preview_only and HARDWARE_AVAILABLE:
            try:
                self.driver = ST7796S(settings)
                logger.info("Hardware display initialized")
            except Exception as e:
                logger.error(f"Failed to initialize hardware display: {e}")
                self.driver = None
        else:
            if not HARDWARE_AVAILABLE:
                logger.warning("Hardware libraries not found. Running in preview mode.")
            else:
                logger.info("Running in preview-only mode")

        # Globe geometry (computed once during frame generation)
        self.globe_scale = THEME.globe.scale
        self.iss_orbit_scale = THEME.globe.iss_orbit_scale
        self.globe_center_x = self.width // 2
        self.globe_center_y = self.height // 2
        self.globe_radius_px = int(min(self.width, self.height) * self.globe_scale) // 2

        # Pre-rendered frame caches
        self.frame_cache: List[Image.Image] = []
        self.frame_bytes_cache: List[bytes] = []
        self.num_frames = THEME.globe.num_frames
        self.frames_generated = False

        # Time-based rotation (decouples speed from frame count)
        self._rotation_start_time: float = time.time()
        self._rotation_period: float = THEME.globe.rotation_period_sec

        # Cache directory
        self.cache_dir = self.settings.preview_dir.parent / "frame_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Load or generate globe frames
        self._load_or_generate_frames()
        self._precompute_rgb565()

        # Reusable frame buffer — avoids per-frame allocation
        self._frame_buf = bytearray(self.width * self.height * 2)

        # HUD setup
        self._init_hud()

        # Preview frame counter
        self._preview_frame_count = 0

    def reinit(self):
        """Re-initialize the display hardware (called by main loop on persistent errors)."""
        if self.driver:
            self.driver._full_reinit()

    def maybe_run_maintenance(self):
        """Run periodic display maintenance if due (call between frames)."""
        if self.driver:
            self.driver.maybe_run_maintenance()

    # ─── HUD ──────────────────────────────────────────────────────────────

    def _init_hud(self):
        """Initialize HUD fonts, resolve per-element styles, and prepare caches."""
        hud = THEME.hud

        # ── Find default font ──
        self._default_font_path: Optional[str] = None
        for path in hud.font_search_paths:
            try:
                ImageFont.truetype(path, 12)  # probe
                self._default_font_path = path
                logger.info(f"Loaded HUD font: {path}")
                break
            except (OSError, IOError):
                continue
        if self._default_font_path is None:
            logger.warning("Using default bitmap font for HUD")

        self._font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}

        # ── Resolve all elements through the cascade ──
        self._resolved: dict[str, _ResolvedElement] = {}
        for bar, names, has_unit in [
            (hud.top, ["lat", "lon", "over"], [False, False, False]),
            (hud.bottom, ["alt", "vel", "age"], [True, True, False]),
        ]:
            for name, unit_flag in zip(names, has_unit):
                element = getattr(bar, name)
                lbl = resolve_text_style("label", element, bar, hud)
                val = resolve_text_style("value", element, bar, hud)

                lbl_font = self._get_font(lbl.font, lbl.size)
                val_font = self._get_font(val.font, val.size)

                unit_resolved = None
                baseline_offset = 0
                if unit_flag:
                    unt = resolve_text_style("unit", element, bar, hud)
                    unt_font = self._get_font(unt.font, unt.size)
                    unit_resolved = _ResolvedText(color=unt.color, font=unt_font)
                    try:
                        baseline_offset = val_font.getmetrics()[0] - unt_font.getmetrics()[0]
                    except Exception:
                        baseline_offset = 4

                self._resolved[name] = _ResolvedElement(
                    label=_ResolvedText(color=lbl.color, font=lbl_font),
                    value=_ResolvedText(color=val.color, font=val_font),
                    unit=unit_resolved,
                    cell_width=element.cell_width,
                    unit_baseline_offset=baseline_offset,
                )

        # ── Cache layout values ──
        self._hud_grid = hud.grid
        self._hud_label_y = hud.label_y
        self._hud_value_y = hud.value_y
        self._hud_unit_gap = hud.unit_gap
        self._hud_bg = hud.background
        self._hud_top_height = hud.top.height
        self._hud_bot_height = hud.bottom.height
        self._hud_top_border = resolve_border_color(hud.top, hud)
        self._hud_bot_border = resolve_border_color(hud.bottom, hud)

        # Cached HUD state: track what's currently rendered to avoid redraws
        self._hud_cache_key: Optional[str] = None
        self._hud_top_bytes: Optional[bytes] = None
        self._hud_bottom_bytes: Optional[bytes] = None

    def _get_font(self, font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
        """Load a font at a given size, using the cache."""
        path = font_path or self._default_font_path
        if path is None:
            return ImageFont.load_default()
        key = (path, size)
        if key not in self._font_cache:
            self._font_cache[key] = ImageFont.truetype(path, size)
        return self._font_cache[key]

    def _render_hud_bars(self, telemetry: "ISSFix") -> str:
        """Render top and bottom HUD bars and cache as RGB565 bytes.

        Returns the cache key string so callers can check if it changed.
        """
        lat = telemetry.latitude
        lon = telemetry.longitude
        alt_km = telemetry.altitude_km if telemetry.altitude_km else 420.0
        vel_kmh = telemetry.velocity_kmh if telemetry.velocity_kmh else 27600.0

        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        lat_val = f"{abs(lat):05.2f}\u00b0{lat_dir}"
        lon_val = f"{abs(lon):06.2f}\u00b0{lon_dir}"
        alt_val = f"{alt_km:,.0f}"
        vel_val = f"{vel_kmh:,.0f}"
        age_sec = int(telemetry.data_age_sec)
        age_val = f"{age_sec}s"

        cache_key = f"{lat_val}|{lon_val}|{alt_val}|{vel_val}|{age_sec}"
        if cache_key == self._hud_cache_key:
            return cache_key

        w = self.width
        g = self._hud_grid
        top_h = self._hud_top_height
        bot_h = self._hud_bot_height
        label_y = self._hud_label_y
        value_y = self._hud_value_y

        # ── Top bar ──
        top_img = Image.new('RGB', (w, top_h), self._hud_bg)
        draw = ImageDraw.Draw(top_img)
        draw.line([0, top_h - 1, w, top_h - 1], fill=self._hud_top_border)

        # LAT cell
        lat_el = self._resolved["lat"]
        lat_x = g
        draw.text((lat_x, label_y), "LAT", fill=lat_el.label.color, font=lat_el.label.font)
        draw.text((lat_x, value_y), lat_val, fill=lat_el.value.color, font=lat_el.value.font)

        # LON cell
        lon_el = self._resolved["lon"]
        lon_x = lat_x + lat_el.cell_width + g
        draw.text((lon_x, label_y), "LON", fill=lon_el.label.color, font=lon_el.label.font)
        draw.text((lon_x, value_y), lon_val, fill=lon_el.value.color, font=lon_el.value.font)

        # Region indicator (right-aligned)
        over_el = self._resolved["over"]
        region = get_common_area_name(lat, lon)
        right_edge = w - g
        over_label_w = draw.textbbox((0, 0), "OVER", font=over_el.label.font)[2]
        draw.text((right_edge - over_label_w, label_y), "OVER", fill=over_el.label.color, font=over_el.label.font)
        # Render multi-word regions with a tighter gap than the mono font's
        # full-width space (e.g. "N. America" → "N." + small gap + "America").
        words = region.split(" ")
        if len(words) > 1:
            space_w = draw.textbbox((0, 0), " ", font=over_el.value.font)[2]
            tight_gap = max(1, space_w // 3)
            word_widths = [draw.textbbox((0, 0), w_, font=over_el.value.font)[2] for w_ in words]
            total_w = sum(word_widths) + tight_gap * (len(words) - 1)
            x = right_edge - total_w
            for i, w_ in enumerate(words):
                draw.text((x, value_y), w_, fill=over_el.value.color, font=over_el.value.font)
                x += word_widths[i] + tight_gap
        else:
            region_text_w = draw.textbbox((0, 0), region, font=over_el.value.font)[2]
            draw.text((right_edge - region_text_w, value_y), region, fill=over_el.value.color, font=over_el.value.font)

        # ── Bottom bar ──
        bot_img = Image.new('RGB', (w, bot_h), self._hud_bg)
        draw = ImageDraw.Draw(bot_img)
        draw.line([0, 0, w, 0], fill=self._hud_bot_border)

        # ALT cell
        alt_el = self._resolved["alt"]
        alt_x = g
        draw.text((alt_x, label_y), "ALT", fill=alt_el.label.color, font=alt_el.label.font)
        draw.text((alt_x, value_y), alt_val, fill=alt_el.value.color, font=alt_el.value.font)
        alt_text_w = draw.textbbox((0, 0), alt_val, font=alt_el.value.font)[2]
        draw.text((alt_x + alt_text_w + self._hud_unit_gap, value_y + alt_el.unit_baseline_offset),
                  "km", fill=alt_el.unit.color, font=alt_el.unit.font)

        # VEL cell
        vel_el = self._resolved["vel"]
        vel_x = alt_x + alt_el.cell_width + g
        draw.text((vel_x, label_y), "VEL", fill=vel_el.label.color, font=vel_el.label.font)
        draw.text((vel_x, value_y), vel_val, fill=vel_el.value.color, font=vel_el.value.font)
        vel_text_w = draw.textbbox((0, 0), vel_val, font=vel_el.value.font)[2]
        draw.text((vel_x + vel_text_w + self._hud_unit_gap, value_y + vel_el.unit_baseline_offset),
                  "km/h", fill=vel_el.unit.color, font=vel_el.unit.font)

        # Data age indicator (right-aligned)
        age_el = self._resolved["age"]
        right_edge = w - g
        age_label_w = draw.textbbox((0, 0), "LAST", font=age_el.label.font)[2]
        draw.text((right_edge - age_label_w, label_y), "LAST", fill=age_el.label.color, font=age_el.label.font)
        age_text_w = draw.textbbox((0, 0), age_val, font=age_el.value.font)[2]
        draw.text((right_edge - age_text_w, value_y), age_val, fill=age_el.value.color, font=age_el.value.font)

        # Convert to RGB565 bytes
        self._hud_top_bytes = self._image_to_rgb565_bytes(top_img)
        self._hud_bottom_bytes = self._image_to_rgb565_bytes(bot_img)
        self._hud_cache_key = cache_key

        return cache_key

    # ─── RGB565 conversion ────────────────────────────────────────────────

    @staticmethod
    def _image_to_rgb565_bytes(image: Image.Image) -> bytes:
        """Convert PIL Image to RGB565 bytes for direct display."""
        img_np = np.array(image)
        r = img_np[..., 0].astype(np.uint16)
        g = img_np[..., 1].astype(np.uint16)
        b = img_np[..., 2].astype(np.uint16)
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        return rgb565.astype('>u2').tobytes()

    def _precompute_rgb565(self):
        """Pre-compute RGB565 data for all cached frames.

        Stores both bytes (for direct display) and numpy uint16 arrays
        (for fast inter-frame blending).
        """
        logger.info("Pre-computing RGB565 frame data...")
        self.frame_bytes_cache = []
        for frame in self.frame_cache:
            self.frame_bytes_cache.append(self._image_to_rgb565_bytes(frame))
        logger.info(f"Pre-computed {len(self.frame_bytes_cache)} frames")

    # ─── Frame cache ──────────────────────────────────────────────────────

    def _load_or_generate_frames(self):
        """Load pre-rendered frames from cache or generate them."""
        cache_file = self.cache_dir / f"globe_{self.num_frames}f.npz"

        if cache_file.exists():
            logger.info("Loading cached Earth frames...")
            try:
                data = np.load(cache_file)
                for i in range(self.num_frames):
                    img_array = data[f'frame_{i}']
                    self.frame_cache.append(Image.fromarray(img_array))
                self.frames_generated = True
                # Update globe geometry from first frame
                self._update_globe_geometry()
                logger.info(f"Loaded {len(self.frame_cache)} cached frames")
                return
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}, regenerating...")

        self._generate_frames()

    def _generate_frames(self):
        """Pre-render all Earth rotation frames using Cartopy.

        Uses multiprocessing to spread work across CPU cores and 110m
        resolution features for faster geometry processing.
        """
        import multiprocessing as mp

        # Verify cartopy is available before spawning workers
        try:
            import cartopy  # noqa: F401
        except ImportError:
            raise ImportError(
                "Cartopy and matplotlib are required for frame generation. "
                "Pre-generate frames on a development machine using generate_frames.py, "
                "then copy var/frame_cache/ to the target device."
            )

        # Serialize globe config as a plain dict so it's picklable
        g = THEME.globe
        globe_cfg = {
            'background': g.background,
            'ocean_color': g.ocean_color,
            'land_color': g.land_color,
            'land_border_color': g.land_border_color,
            'land_border_width': g.land_border_width,
            'coastline_color': g.coastline_color,
            'coastline_width': g.coastline_width,
            'grid_color': g.grid_color,
            'grid_width': g.grid_width,
            'grid_alpha': g.grid_alpha,
        }

        degrees_per_frame = 360 / self.num_frames
        work_args = [
            ((i * degrees_per_frame) - 180, self.width, self.height,
             self.globe_scale, globe_cfg)
            for i in range(self.num_frames)
        ]

        n_workers = min(mp.cpu_count(), self.num_frames)
        logger.info(f"Generating {self.num_frames} Earth frames "
                     f"({n_workers} workers, 110m resolution)...")

        self.frame_cache = []
        with mp.Pool(n_workers) as pool:
            for i, frame_array in enumerate(
                pool.imap(self._render_globe_frame_worker, work_args)
            ):
                self.frame_cache.append(Image.fromarray(frame_array))
                if (i + 1) % 10 == 0 or (i + 1) == self.num_frames:
                    logger.info(f"  {i+1}/{self.num_frames} frames done")

        self._update_globe_geometry()

        # Save to cache (uncompressed — much faster to write than gzip)
        logger.info("Saving frames to cache...")
        try:
            frame_dict = {f'frame_{i}': np.array(frame)
                          for i, frame in enumerate(self.frame_cache)}
            np.savez(self.cache_dir / f"globe_{self.num_frames}f.npz", **frame_dict)
            logger.info("Frames cached successfully")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")

        self.frames_generated = True
        logger.info("Frame generation complete!")

    def _update_globe_geometry(self):
        """Compute globe center and radius from the rendered frames."""
        if not self.frame_cache:
            return
        # All frames are the same size, so use the first one
        # The globe is rendered at globe_scale of the smaller dimension
        globe_size = int(min(self.width, self.height) * self.globe_scale)
        self.globe_center_x = self.width // 2
        self.globe_center_y = self.height // 2
        self.globe_radius_px = globe_size // 2

    @staticmethod
    def _render_globe_frame_worker(args: tuple) -> np.ndarray:
        """Render a single globe frame. Multiprocessing-friendly (static).

        Returns the composited frame as a numpy uint8 RGB array.
        """
        central_lon, width, height, globe_scale, globe_cfg = args

        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature

        bg_hex = rgb_to_hex(globe_cfg['background'])
        globe_size = int(min(width, height) * globe_scale)
        dpi = 100
        fig = plt.figure(figsize=(globe_size / dpi, globe_size / dpi), dpi=dpi, facecolor=bg_hex)

        projection = ccrs.Orthographic(central_longitude=central_lon, central_latitude=0)
        ax = fig.add_subplot(1, 1, 1, projection=projection)
        ax.set_facecolor(bg_hex)
        ax.set_global()

        # Use 110m (lowest) resolution for faster geometry processing
        ax.add_feature(cfeature.NaturalEarthFeature(
            'physical', 'ocean', '110m',
            facecolor=rgb_to_hex(globe_cfg['ocean_color']), edgecolor='none'), zorder=0)
        ax.add_feature(cfeature.NaturalEarthFeature(
            'physical', 'land', '110m',
            facecolor=rgb_to_hex(globe_cfg['land_color']),
            edgecolor=rgb_to_hex(globe_cfg['land_border_color']),
            linewidth=globe_cfg['land_border_width']), zorder=1)
        ax.add_feature(cfeature.NaturalEarthFeature(
            'physical', 'coastline', '110m',
            facecolor='none',
            edgecolor=rgb_to_hex(globe_cfg['coastline_color']),
            linewidth=globe_cfg['coastline_width']), zorder=2)
        ax.gridlines(color=rgb_to_hex(globe_cfg['grid_color']),
                      linewidth=globe_cfg['grid_width'],
                      alpha=globe_cfg['grid_alpha'], linestyle='-')
        ax.spines['geo'].set_visible(False)

        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

        # Render to raw RGBA buffer instead of PNG encode/decode round-trip
        fig.canvas.draw()
        rgba = np.asarray(fig.canvas.buffer_rgba())
        plt.close(fig)

        globe_img = rgba[:, :, :3]  # drop alpha channel

        # Composite onto full-size canvas
        final = np.zeros((height, width, 3), dtype=np.uint8)
        final[:, :] = globe_cfg['background']
        x_off = (width - globe_img.shape[1]) // 2
        y_off = (height - globe_img.shape[0]) // 2
        final[y_off:y_off + globe_img.shape[0],
              x_off:x_off + globe_img.shape[1]] = globe_img

        return final

    # ─── ISS marker (RGB565 byte-buffer operations) ──────────────────────

    def _calc_iss_screen_pos(self, lat: float, lon: float, central_lon: float):
        """Calculate ISS screen position, visibility, and opacity.

        Returns (px, py, opacity) or None if ISS is not visible.
        """
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        central_lon_rad = math.radians(central_lon)

        cos_c = math.cos(lat_rad) * math.cos(lon_rad - central_lon_rad)

        horizon_threshold = -math.sqrt(1 - 1 / (self.iss_orbit_scale ** 2))

        if cos_c < horizon_threshold:
            return None

        # Opacity for limb transition
        m = THEME.marker
        fade_start = m.fade_start
        if cos_c < fade_start:
            opacity = (cos_c - horizon_threshold) / (fade_start - horizon_threshold)
            opacity = max(0.0, min(1.0, opacity))
        else:
            opacity = 1.0

        # Surface point in orthographic projection
        x_surface = math.cos(lat_rad) * math.sin(lon_rad - central_lon_rad)
        y_surface = math.sin(lat_rad)

        # ISS position (exaggerated altitude)
        x_iss = x_surface * self.iss_orbit_scale
        y_iss = y_surface * self.iss_orbit_scale

        px = int(self.globe_center_x + x_iss * self.globe_radius_px)
        py = int(self.globe_center_y - y_iss * self.globe_radius_px)

        if not (0 <= px < self.width and 0 <= py < self.height):
            return None

        # Check occlusion when marker is inside Earth disk on back side
        if cos_c < 0:
            dist = math.sqrt((px - self.globe_center_x)**2 + (py - self.globe_center_y)**2)
            if dist < self.globe_radius_px:
                occlusion = 1.0 - (self.globe_radius_px - dist) / self.globe_radius_px
                opacity *= occlusion * m.occlusion_factor

        if opacity < m.opacity_cutoff:
            return None

        return (px, py, opacity)

    def _draw_iss_marker_rgb565(self, frame_buf: bytearray, px: int, py: int, opacity: float):
        """Draw ISS marker directly into an RGB565 byte buffer.

        Draws concentric glow rings + core + center dot using direct byte writes.
        """
        m = THEME.marker
        size_scale = m.min_size_scale + (m.max_size_scale - m.min_size_scale) * opacity
        w = self.width

        # Pre-compute marker pixel colors at varying radii
        # Glow rings (outer to inner)
        rings = []
        for i in range(m.ring_count):
            r = int((m.outer_ring_radius - i * m.ring_step) * size_scale)
            if r < 1:
                continue
            base_brightness = int((m.ring_brightness_base + i * m.ring_brightness_step) * opacity)
            color = _rgb_to_rgb565(int(m.glow_color[0] * opacity), base_brightness, base_brightness)
            rings.append((r * r, color))

        core_r = max(1, int(m.core_radius * size_scale))
        core_color = _rgb_to_rgb565(int(m.core_color[0] * opacity), 0, 0)

        show_center = opacity > m.center_dot_opacity_threshold
        center_b = int(m.center_color[0] * opacity)
        center_color = _rgb_to_rgb565(center_b, center_b, center_b)

        # Determine bounding box for the marker
        max_r = int(m.outer_ring_radius * size_scale) + 1
        y_start = max(0, py - max_r)
        y_end = min(self.height - 1, py + max_r)
        x_start = max(0, px - max_r)
        x_end = min(self.width - 1, px + max_r)

        for my in range(y_start, y_end + 1):
            dy = my - py
            row_offset = my * w * 2
            for mx in range(x_start, x_end + 1):
                dx = mx - px
                dist_sq = dx * dx + dy * dy

                color = None

                # Center dot (radius 1)
                if show_center and dist_sq <= 1:
                    color = center_color
                # Core (radius core_r)
                elif dist_sq <= core_r * core_r:
                    color = core_color
                else:
                    # Check glow rings (outer to inner — outer drawn first, inner overwrites)
                    for ring_r_sq, ring_color in rings:
                        if dist_sq <= ring_r_sq:
                            color = ring_color
                            break

                if color is not None:
                    offset = row_offset + mx * 2
                    frame_buf[offset] = (color >> 8) & 0xFF
                    frame_buf[offset + 1] = color & 0xFF

    def _patch_hud_bytes(self, frame_buf: bytearray):
        """Patch cached HUD bar bytes into a frame buffer."""
        if self._hud_top_bytes is None or self._hud_bottom_bytes is None:
            return

        top_size = self.width * self._hud_top_height * 2
        bot_size = self.width * self._hud_bot_height * 2
        bot_offset = (self.height - self._hud_bot_height) * self.width * 2

        # Top bar: rows 0..top_height
        frame_buf[0:top_size] = self._hud_top_bytes

        # Bottom bar: rows (height - bot_height)..height
        frame_buf[bot_offset:bot_offset + bot_size] = self._hud_bottom_bytes

    # ─── Main update loop entry point ─────────────────────────────────────

    def update_with_telemetry(self, telemetry: "ISSFix"):
        """Update the display with current ISS telemetry.

        Optimized pipeline:
        1. Copy pre-computed RGB565 bytes into reusable buffer
        2. Draw ISS marker directly into byte buffer (small area)
        3. Patch cached HUD bars into byte buffer (memcpy)
        4. Send to display
        """
        if not self.frames_generated:
            logger.warning("Frames not yet generated")
            return

        # Ensure HUD bars are rendered (only redraws when values change)
        self._render_hud_bars(telemetry)

        # Select globe frame based on elapsed time (decouples speed from frame count)
        elapsed = time.time() - self._rotation_start_time
        rotation_progress = (elapsed % self._rotation_period) / self._rotation_period
        current_frame = int(rotation_progress * self.num_frames) % self.num_frames

        # Copy pre-computed RGB565 bytes into reusable buffer (no allocation)
        self._frame_buf[:] = self.frame_bytes_cache[current_frame]

        # Calculate ISS screen position for this frame's view angle
        central_lon = (current_frame * (360 / self.num_frames)) - 180
        iss_pos = self._calc_iss_screen_pos(telemetry.latitude, telemetry.longitude, central_lon)

        # Draw ISS marker directly into byte buffer
        if iss_pos is not None:
            px, py, opacity = iss_pos
            self._draw_iss_marker_rgb565(self._frame_buf, px, py, opacity)

        # Patch HUD bars into byte buffer
        self._patch_hud_bytes(self._frame_buf)

        # Send to display
        if self.driver:
            self.driver.display_raw(self._frame_buf)

        # Preview mode: save occasional PNGs
        if self.driver is None:
            self._preview_frame_count += 1
            if self._preview_frame_count % 30 == 1:
                self._save_preview(self._frame_buf)

    def _save_preview(self, pixel_bytes: Union[bytes, bytearray]):
        """Save an RGB565 frame buffer as a PNG preview image."""
        try:
            arr = np.frombuffer(pixel_bytes, dtype='>u2').reshape(self.height, self.width)
            r = ((arr >> 11) & 0x1F).astype(np.uint8) * 8
            g = ((arr >> 5) & 0x3F).astype(np.uint8) * 4
            b = (arr & 0x1F).astype(np.uint8) * 8
            rgb = np.stack([r, g, b], axis=-1)
            img = Image.fromarray(rgb)
            preview_path = self.settings.preview_dir / f"frame_{self._preview_frame_count:06d}.png"
            img.save(preview_path)
            logger.debug(f"Preview saved: {preview_path}")
        except Exception as e:
            logger.warning(f"Failed to save preview: {e}")

    def close(self):
        if self.driver:
            self.driver.close()
