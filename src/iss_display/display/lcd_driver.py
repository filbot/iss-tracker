import logging
import math
import time
from pathlib import Path
from typing import Optional, List, Union, TYPE_CHECKING
import io

from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

if TYPE_CHECKING:
    from iss_display.data.iss_client import ISSFix

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    CARTOPY_AVAILABLE = True
except ImportError:
    CARTOPY_AVAILABLE = False

try:
    import spidev
    import RPi.GPIO as GPIO
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False

from iss_display.config import Settings
from iss_display.theme import THEME, rgb_to_hex

logger = logging.getLogger(__name__)

# ISS orbital period (~92.68 minutes)
ISS_ORBITAL_PERIOD_SEC = 92.68 * 60
ORBITS_PER_DAY = 86400 / ISS_ORBITAL_PERIOD_SEC

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

        self._init_gpio()
        self._init_spi()
        self._init_display()

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

    def _reset(self):
        GPIO.output(self.rst, GPIO.HIGH)
        time.sleep(0.01)
        GPIO.output(self.rst, GPIO.LOW)
        time.sleep(0.01)
        GPIO.output(self.rst, GPIO.HIGH)
        time.sleep(0.12)

    def command(self, cmd: int):
        GPIO.output(self.dc, GPIO.LOW)
        self.spi.writebytes([cmd])

    def data(self, data: int):
        GPIO.output(self.dc, GPIO.HIGH)
        self.spi.writebytes([data])

    def _init_display(self):
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

        GPIO.output(self.bl, GPIO.HIGH)

        self.set_window(0, 0, self.width - 1, self.height - 1)
        self._fill(0x0000)

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
        """Display pre-converted RGB565 data directly."""
        self.command(RAMWR)
        GPIO.output(self.dc, GPIO.HIGH)
        self.spi.writebytes2(pixel_bytes)

    def close(self):
        """Properly shut down the display."""
        try:
            GPIO.output(self.bl, GPIO.LOW)

            black_screen = bytes(self.width * self.height * 2)
            for _ in range(3):
                self.set_window(0, 0, self.width - 1, self.height - 1)
                self.command(RAMWR)
                GPIO.output(self.dc, GPIO.HIGH)
                self.spi.writebytes2(black_screen)
                time.sleep(0.05)

            self.command(DISPOFF)
            time.sleep(0.05)

            self.command(SLPIN)
            time.sleep(0.12)

            GPIO.output(self.rst, GPIO.LOW)
            GPIO.output(self.bl, GPIO.LOW)

            logger.info("Display turned off and cleared")
        except Exception as e:
            logger.warning(f"Error during display shutdown: {e}")
        finally:
            try:
                self.spi.close()
            except Exception:
                pass
            GPIO.cleanup()


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

        if not CARTOPY_AVAILABLE:
            raise ImportError("Cartopy is required for rendering the globe")

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

        # HUD setup
        self._init_hud()

        # Preview frame counter
        self._preview_frame_count = 0

    # ─── HUD ──────────────────────────────────────────────────────────────

    def _init_hud(self):
        """Initialize HUD fonts, colors, and cached bars."""
        typo = THEME.hud_typography
        self.hud_font_value_size = typo.value_size
        self.hud_font_unit_size = typo.unit_size
        self.hud_font_label_size = typo.label_size

        mono_fonts = list(typo.font_search_paths)

        self.hud_font = None
        self.hud_font_sm = None
        self.hud_font_lbl = None

        for font_path in mono_fonts:
            try:
                self.hud_font = ImageFont.truetype(font_path, self.hud_font_value_size)
                self.hud_font_sm = ImageFont.truetype(font_path, self.hud_font_unit_size)
                self.hud_font_lbl = ImageFont.truetype(font_path, self.hud_font_label_size)
                logger.info(f"Loaded HUD font: {font_path}")
                break
            except (OSError, IOError):
                continue

        if self.hud_font is None:
            self.hud_font = ImageFont.load_default()
            self.hud_font_sm = self.hud_font
            self.hud_font_lbl = self.hud_font
            logger.warning("Using default bitmap font for HUD")

        # Color palette
        c = THEME.hud_colors
        self.hud_color_primary = c.primary
        self.hud_color_label = c.label
        self.hud_color_dim = c.dim
        self.hud_color_border = c.border
        self.hud_color_bg = c.background
        self.hud_color_indicator = c.indicator

        # Layout grid
        lay = THEME.hud_layout
        self.hud_grid = lay.grid
        self.hud_top_height = lay.top_bar_height
        self.hud_bottom_height = lay.bottom_bar_height

        # Cached HUD state: track what's currently rendered to avoid redraws
        self._hud_cache_key: Optional[str] = None
        self._hud_top_bytes: Optional[bytes] = None
        self._hud_bottom_bytes: Optional[bytes] = None

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
        orb_val = f"{ORBITS_PER_DAY:.0f}"

        cache_key = f"{lat_val}|{lon_val}|{alt_val}|{vel_val}"
        if cache_key == self._hud_cache_key:
            return cache_key

        w = self.width
        g = self.hud_grid
        top_h = self.hud_top_height
        bot_h = self.hud_bottom_height

        # ── Top bar ──
        top_img = Image.new('RGB', (w, top_h), self.hud_color_bg)
        draw = ImageDraw.Draw(top_img)
        draw.line([0, top_h - 1, w, top_h - 1], fill=self.hud_color_border)

        label_y = g
        value_y = g * 3

        # LAT cell
        lay = THEME.hud_layout
        lat_x = g
        lat_w = lay.lat_cell_width
        draw.text((lat_x, label_y), "LAT", fill=self.hud_color_label, font=self.hud_font_lbl)
        lat_bbox = draw.textbbox((0, 0), lat_val, font=self.hud_font)
        draw.text((lat_x + lat_w - (lat_bbox[2] - lat_bbox[0]), value_y),
                  lat_val, fill=self.hud_color_primary, font=self.hud_font)

        # LON cell
        lon_x = lat_x + lat_w + g
        lon_w = lay.lon_cell_width
        draw.text((lon_x, label_y), "LON", fill=self.hud_color_label, font=self.hud_font_lbl)
        lon_bbox = draw.textbbox((0, 0), lon_val, font=self.hud_font)
        draw.text((lon_x + lon_w - (lon_bbox[2] - lon_bbox[0]), value_y),
                  lon_val, fill=self.hud_color_primary, font=self.hud_font)

        # ISS indicator
        iss_w = lay.iss_cell_width
        iss_x = w - g - iss_w
        iss_center_y = top_h // 2
        dot_r = lay.indicator_dot_radius
        draw.ellipse([iss_x, iss_center_y - dot_r, iss_x + dot_r * 2, iss_center_y + dot_r],
                     fill=self.hud_color_indicator)
        draw.text((iss_x + dot_r * 2 + 4, iss_center_y - 8), "ISS",
                  fill=self.hud_color_primary, font=self.hud_font_sm)

        # ── Bottom bar ──
        bot_img = Image.new('RGB', (w, bot_h), self.hud_color_bg)
        draw = ImageDraw.Draw(bot_img)
        draw.line([0, 0, w, 0], fill=self.hud_color_border)

        label_y = g
        value_y = g * 3

        # ALT cell
        alt_x = g
        alt_w = lay.alt_cell_width
        draw.text((alt_x, label_y), "ALT", fill=self.hud_color_label, font=self.hud_font_lbl)
        alt_bbox = draw.textbbox((0, 0), alt_val, font=self.hud_font)
        val_x = alt_x + alt_w - (alt_bbox[2] - alt_bbox[0]) - 22
        draw.text((val_x, value_y), alt_val, fill=self.hud_color_primary, font=self.hud_font)
        draw.text((alt_x + alt_w - 18, value_y + 4), "km", fill=self.hud_color_dim, font=self.hud_font_sm)

        # VEL cell
        vel_x = alt_x + alt_w + g
        vel_w = lay.vel_cell_width
        draw.text((vel_x, label_y), "VEL", fill=self.hud_color_label, font=self.hud_font_lbl)
        vel_bbox = draw.textbbox((0, 0), vel_val, font=self.hud_font)
        val_x = vel_x + vel_w - (vel_bbox[2] - vel_bbox[0]) - 32
        draw.text((val_x, value_y), vel_val, fill=self.hud_color_primary, font=self.hud_font)
        draw.text((vel_x + vel_w - 28, value_y + 4), "km/h", fill=self.hud_color_dim, font=self.hud_font_sm)

        # ORBIT indicator
        orb_w = lay.orb_cell_width
        orb_x = w - g - orb_w
        orb_center_y = bot_h // 2
        draw.text((orb_x, orb_center_y - 12), orb_val, fill=self.hud_color_dim, font=self.hud_font_sm)
        draw.text((orb_x, orb_center_y + 2), "ORB/D", fill=self.hud_color_label, font=self.hud_font_lbl)

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
        """Pre-compute RGB565 bytes for all cached frames."""
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
        """Pre-render all Earth rotation frames using Cartopy."""
        logger.info(f"Generating {self.num_frames} Earth frames with Cartopy...")

        self.frame_cache = []
        degrees_per_frame = 360 / self.num_frames

        for i in range(self.num_frames):
            central_lon = (i * degrees_per_frame) - 180
            logger.info(f"  Generating frame {i+1}/{self.num_frames} (lon={central_lon:.0f}\u00b0)...")
            frame = self._render_globe_frame(central_lon)
            self.frame_cache.append(frame)

        # Update globe geometry from the rendered frames
        self._update_globe_geometry()

        # Save to cache
        logger.info("Saving frames to cache...")
        try:
            frame_dict = {f'frame_{i}': np.array(frame) for i, frame in enumerate(self.frame_cache)}
            np.savez_compressed(self.cache_dir / f"globe_{self.num_frames}f.npz", **frame_dict)
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

    def _render_globe_frame(self, central_lon: float, central_lat: float = 0) -> Image.Image:
        """Render a single globe frame at the given central longitude."""
        g = THEME.globe
        bg_hex = rgb_to_hex(g.background)

        globe_size = int(min(self.width, self.height) * self.globe_scale)
        dpi = 100
        fig = plt.figure(figsize=(globe_size/dpi, globe_size/dpi), dpi=dpi, facecolor=bg_hex)

        projection = ccrs.Orthographic(central_longitude=central_lon, central_latitude=central_lat)
        ax = fig.add_subplot(1, 1, 1, projection=projection)
        ax.set_facecolor(bg_hex)
        ax.set_global()

        ax.add_feature(cfeature.OCEAN, facecolor=rgb_to_hex(g.ocean_color), edgecolor='none', zorder=0)
        ax.add_feature(cfeature.LAND, facecolor=rgb_to_hex(g.land_color),
                        edgecolor=rgb_to_hex(g.land_border_color),
                        linewidth=g.land_border_width, zorder=1)
        ax.add_feature(cfeature.COASTLINE, edgecolor=rgb_to_hex(g.coastline_color),
                        linewidth=g.coastline_width, zorder=2)
        ax.gridlines(color=rgb_to_hex(g.grid_color), linewidth=g.grid_width,
                      alpha=g.grid_alpha, linestyle='-')
        ax.spines['geo'].set_visible(False)

        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=dpi, facecolor=bg_hex, edgecolor='none',
                    bbox_inches='tight', pad_inches=0)
        plt.close(fig)
        buf.seek(0)

        globe_img = Image.open(buf).convert('RGB')
        buf.close()

        final_img = Image.new('RGB', (self.width, self.height), g.background)
        x_offset = (self.width - globe_img.width) // 2
        y_offset = (self.height - globe_img.height) // 2
        final_img.paste(globe_img, (x_offset, y_offset))

        return final_img

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

        top_size = self.width * self.hud_top_height * 2
        bot_size = self.width * self.hud_bottom_height * 2
        bot_offset = (self.height - self.hud_bottom_height) * self.width * 2

        # Top bar: rows 0..top_height
        frame_buf[0:top_size] = self._hud_top_bytes

        # Bottom bar: rows (height - bot_height)..height
        frame_buf[bot_offset:bot_offset + bot_size] = self._hud_bottom_bytes

    # ─── Main update loop entry point ─────────────────────────────────────

    def update_with_telemetry(self, telemetry: "ISSFix"):
        """Update the display with current ISS telemetry.

        Optimized pipeline:
        1. Copy pre-computed RGB565 bytes for current globe frame
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

        # Start with pre-computed RGB565 bytes for this globe frame
        frame_buf = bytearray(self.frame_bytes_cache[current_frame])

        # Calculate ISS screen position for this frame's view angle
        central_lon = (current_frame * (360 / self.num_frames)) - 180
        iss_pos = self._calc_iss_screen_pos(telemetry.latitude, telemetry.longitude, central_lon)

        # Draw ISS marker directly into byte buffer
        if iss_pos is not None:
            px, py, opacity = iss_pos
            self._draw_iss_marker_rgb565(frame_buf, px, py, opacity)

        # Patch HUD bars into byte buffer
        self._patch_hud_bytes(frame_buf)

        # Send to display
        if self.driver:
            self.driver.display_raw(frame_buf)

        # Preview mode: save occasional PNGs
        if self.driver is None:
            self._preview_frame_count += 1
            if self._preview_frame_count % 30 == 1:
                self._save_preview(frame_buf)

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
