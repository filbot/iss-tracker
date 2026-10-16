import logging
import math
import threading
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

# Recovery constants
_MAX_RECOVERY_ATTEMPTS = 3

# Frame index resync threshold: snap to wall-clock if we've fallen this many
# frames behind. Below the threshold, we advance one frame per render to
# eliminate visible angular jumps under transient stalls.
_FRAME_RESYNC_THRESHOLD = 4

# Preview mode keeps a rolling set of this many PNGs instead of writing a
# new file forever.
_MAX_PREVIEW_FILES = 100

# Telemetry older than this is flagged on the HUD: the OVER cell switches to
# an amber NO LINK indicator and the LAST readout goes humanized (12m / 47h).
_STALE_DATA_SEC = 600.0


def _format_age(age_sec: int) -> str:
    """Humanize a data age: 38s → 12m → 47h. Keeps the LAST cell readable
    during long outages instead of overflowing with a raw seconds count."""
    if age_sec < 60:
        return f"{age_sec}s"
    if age_sec < 3600:
        return f"{age_sec // 60}m"
    return f"{age_sec // 3600}h"

RGB = Tuple[int, int, int]

# Characters pre-rendered into each HUD glyph atlas. Covers telemetry values
# (digits, degree, N/S/E/W, units), labels, and every region name that
# geography.get_common_area_name can return, plus NO LINK.
_ATLAS_CHARSET = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    " .,°'/-"
)


@dataclass
class _ResolvedText:
    """Fully resolved text style with loaded PIL font and glyph atlas.

    The atlas maps each character to a pre-rendered RGB565 big-endian tile
    (glyph on the HUD background color). Runtime HUD composition pastes
    these tiles with numpy instead of calling PIL text rendering, which
    holds the GIL for ~30 ms per HUD pass and stalled the render thread
    once per composer tick (the "hitch every second" bug).
    """
    color: RGB
    font: ImageFont.FreeTypeFont
    atlas: Optional[dict] = None


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

        # Pre-allocated single-byte buffers: avoid per-call list allocation in command()/data()
        self._cmd_buf = bytearray(1)
        self._dat_buf = bytearray(1)
        # Pre-allocated 4-byte buffers for CASET/RASET window commands
        self._caset_data = bytearray(4)
        self._raset_data = bytearray(4)

        # Set to True by _recover() so the LcdDisplay wrapper can force a full
        # frame on the next render call to resync after a reset.
        self.reinit_occurred = False

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
        self._cmd_buf[0] = cmd
        self.spi.writebytes2(self._cmd_buf)

    def data(self, val: int):
        GPIO.output(self.dc, GPIO.HIGH)
        self._dat_buf[0] = val
        self.spi.writebytes2(self._dat_buf)

    def _init_display(self, *, first_boot: bool = False):
        logger.info("Display init: hardware reset")
        self._reset()

        self.command(SWRESET)
        time.sleep(0.15)

        self.command(SLPOUT)
        time.sleep(0.15)
        logger.info("Display init: SWRESET + SLPOUT done")

        self.command(COLMOD)
        self.data(0x55)  # 16-bit/pixel

        # Memory Access Control: MX=1, BGR=1
        self.command(MADCTL)
        self.data(0x48)

        self.command(INVON)

        self.command(NORON)
        time.sleep(0.01)

        self.command(DISPON)
        time.sleep(0.12)
        logger.info("Display init: DISPON done")

        if first_boot:
            GPIO.output(self.bl, GPIO.HIGH)
            # Diagnostic: red fill to verify display hardware is responding
            logger.info("Display init: filling RED test pattern")
            self._fill(0xF800)  # RGB565 red
            time.sleep(1.0)
            logger.info("Display init: filling BLACK")
            self._fill(0x0000)

        # On non-first-boot (recovery), the caller (_recover) sets
        # reinit_occurred = True so the next render entry point forces
        # a full frame. We don't leave a dangling set_window/RAMWR here.

        self._consecutive_failures = 0
        logger.info("Display initialized")

    def _recover(self):
        """Attempt to recover SPI bus and display from a failed state."""
        logger.warning(f"Attempting SPI/display recovery (failures: {self._consecutive_failures})...")
        try:
            self.spi.close()
        except Exception:
            logger.debug("SPI close during recovery failed", exc_info=True)

        time.sleep(0.1)

        try:
            self._init_spi()
            if self._consecutive_failures >= _MAX_RECOVERY_ATTEMPTS:
                logger.warning("Multiple failures, performing hardware reset")
                self._reset()
                time.sleep(0.2)
            self._init_display()
            self.reinit_occurred = True  # signal LcdDisplay to force a full frame
            logger.info("SPI/display recovery successful")
        except Exception:
            # Leave reinit_occurred False. The caller re-raises the original
            # SPI error, so the renderer's _REINIT/_EXIT_AFTER_ERRORS
            # escalation counts it and eventually exits for a systemd restart.
            logger.exception("Recovery failed; will retry on next SPI failure")

    def _fill(self, color: int):
        """Fill the entire screen with a solid color (RGB565)."""
        self.set_window(0, 0, self.width - 1, self.height - 1)
        high = (color >> 8) & 0xFF
        low = color & 0xFF
        pixel_data = bytes([high, low] * (self.width * self.height))
        logger.info(f"_fill: color=0x{color:04X}, {len(pixel_data)} bytes, "
                    f"DC pin will be set HIGH")
        GPIO.output(self.dc, GPIO.HIGH)
        self.spi.writebytes2(pixel_data)
        logger.info("_fill: SPI write complete")

    def set_window(self, x0, y0, x1, y1):
        # CASET — send command then 4 data bytes in one burst
        self._cmd_buf[0] = CASET
        GPIO.output(self.dc, GPIO.LOW)
        self.spi.writebytes2(self._cmd_buf)
        GPIO.output(self.dc, GPIO.HIGH)
        d = self._caset_data
        d[0] = x0 >> 8; d[1] = x0 & 0xFF; d[2] = x1 >> 8; d[3] = x1 & 0xFF
        self.spi.writebytes2(d)

        # RASET
        self._cmd_buf[0] = RASET
        GPIO.output(self.dc, GPIO.LOW)
        self.spi.writebytes2(self._cmd_buf)
        GPIO.output(self.dc, GPIO.HIGH)
        d = self._raset_data
        d[0] = y0 >> 8; d[1] = y0 & 0xFF; d[2] = y1 >> 8; d[3] = y1 & 0xFF
        self.spi.writebytes2(d)

        # RAMWR
        self._cmd_buf[0] = RAMWR
        GPIO.output(self.dc, GPIO.LOW)
        self.spi.writebytes2(self._cmd_buf)

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
            # Propagate so the renderer's error escalation sees the failure;
            # swallowing it here kept a permanently broken bus "healthy".
            raise

    def display_region(self, x0: int, y0: int, x1: int, y1: int, frame_buf_np: "np.ndarray"):
        """Send a rectangular sub-region from frame_buf_np to the display.

        Used for partial updates (ISS marker erase/redraw) to avoid sending
        the full 307 KB frame when only a small area changed.
        """
        region_bytes = np.ascontiguousarray(frame_buf_np[y0:y1 + 1, x0:x1 + 1]).tobytes()
        try:
            self.set_window(x0, y0, x1, y1)
            GPIO.output(self.dc, GPIO.HIGH)
            self.spi.writebytes2(region_bytes)
            self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            logger.error(f"SPI region write failed ({self._consecutive_failures}x): {e}")
            self._recover()
            raise

    def close(self):
        """Properly shut down the display with robust error handling."""
        # Backlight off first — immediate visual feedback
        try:
            GPIO.output(self.bl, GPIO.LOW)
        except Exception:
            logger.debug("Backlight-off during shutdown failed", exc_info=True)

        # Clear screen (single fill, IPS panels don't ghost)
        try:
            black_screen = bytes(self.width * self.height * 2)
            self.set_window(0, 0, self.width - 1, self.height - 1)
            GPIO.output(self.dc, GPIO.HIGH)
            self.spi.writebytes2(black_screen)
            time.sleep(0.05)
        except Exception:
            logger.debug("Screen clear during shutdown failed", exc_info=True)

        # Display off command
        try:
            self.command(DISPOFF)
            time.sleep(0.05)
        except Exception:
            logger.debug("DISPOFF during shutdown failed", exc_info=True)

        # Sleep mode
        try:
            self.command(SLPIN)
            time.sleep(0.12)
        except Exception:
            logger.debug("SLPIN during shutdown failed", exc_info=True)

        # Hardware reset — guarantees known state for next startup
        try:
            GPIO.output(self.rst, GPIO.LOW)
            time.sleep(0.05)
        except Exception:
            logger.debug("Reset-low during shutdown failed", exc_info=True)

        # Release hardware resources
        try:
            self.spi.close()
        except Exception:
            logger.debug("SPI close during shutdown failed", exc_info=True)

        try:
            GPIO.cleanup()
        except Exception:
            logger.debug("GPIO cleanup during shutdown failed", exc_info=True)

        logger.info("Display shut down cleanly")


class LcdDisplay:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.width = settings.display_width
        self.height = settings.display_height
        self._bytes_per_row = self.width * 2  # 2 bytes per pixel (RGB565)

        self.driver: Optional[ST7796S] = None
        if not settings.preview_only and HARDWARE_AVAILABLE:
            # Let init failures propagate: falling back to preview mode here
            # ran the service headless with a dark screen while it filled the
            # SD card with preview PNGs. Exiting lets systemd retry instead.
            self.driver = ST7796S(settings)
            logger.info("Hardware display initialized")
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

        # Globe disc bbox — sent on globe-only frames to avoid retransmitting
        # the static HUD bars. Matches the rendering offset in _generate_frames.
        globe_size = int(min(self.width, self.height) * self.globe_scale)
        gx0 = (self.width - globe_size) // 2
        gy0 = (self.height - globe_size) // 2
        self._globe_disc_bbox: Tuple[int, int, int, int] = (
            gx0, gy0, gx0 + globe_size - 1, gy0 + globe_size - 1,
        )

        # Pre-rendered frame caches. frame_cache (RGB uint8 arrays) only
        # exists during generation/conversion; render time uses
        # frame_np_cache (RGB565 big-endian numpy arrays) exclusively.
        self.frame_cache: List[np.ndarray] = []
        self.frame_np_cache: List[np.ndarray] = []
        self.num_frames = THEME.globe.num_frames
        self.frames_generated = False

        # Time-based rotation (decouples speed from frame count). Monotonic,
        # so an NTP step can't jump the globe or stall the frame-aligned wait.
        self._rotation_start_time: float = time.monotonic()
        self._rotation_period: float = THEME.globe.rotation_period_sec

        # Cache directory
        self.cache_dir = self.settings.preview_dir.parent / "frame_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Load or generate globe frames
        self._load_or_generate_frames()
        self._precompute_rgb565()

        # Reusable frame buffer — avoids per-frame allocation
        self._frame_buf = bytearray(self.width * self.height * 2)
        # Writable big-endian uint16 numpy view of the same memory (zero-copy).
        # Writes to _frame_buf_np go directly to _frame_buf used by display_raw().
        self._frame_buf_np = np.frombuffer(self._frame_buf, dtype='>u2').reshape(self.height, self.width)

        # Partial-update state
        self._prev_frame_idx: Optional[int] = None
        self._prev_marker_bbox: Optional[Tuple[int, int, int, int]] = None  # (x0, y0, x1, y1)
        self._force_full_frame: bool = True  # first frame is always a full write

        # Tracks which HUD-bar version was most recently transmitted to the
        # display, so the render thread can detect when the composer has
        # produced a new version that needs to be sent.
        self._last_sent_top_version: int = 0
        self._last_sent_bottom_version: int = 0

        # Pre-allocated marker drawing buffers (avoids per-frame numpy allocations)
        m = THEME.marker
        max_marker_r = int(m.outer_ring_radius * m.max_size_scale) + 1
        max_marker_dim = 2 * max_marker_r + 1
        # Pre-compute full distance-squared grid centered at origin (used via slicing)
        _dy = np.arange(-max_marker_r, max_marker_r + 1, dtype=np.int32)
        _dx = np.arange(-max_marker_r, max_marker_r + 1, dtype=np.int32)
        self._marker_dist_sq_full = _dy[:, None] ** 2 + _dx[None, :] ** 2
        self._marker_max_r = max_marker_r
        self._marker_color_buf = np.zeros((max_marker_dim, max_marker_dim), dtype=np.uint16)
        self._marker_mask = np.zeros((max_marker_dim, max_marker_dim), dtype=np.bool_)

        # HUD setup
        self._init_hud()

        # Crew view setup
        self._init_crew_view()

        # Preview counters: frames rendered, and PNGs written (picks the
        # rolling file slot)
        self._preview_frame_count = 0
        self._preview_saves = 0

    def reinit(self):
        """Re-initialize the display hardware (called by render thread on persistent errors)."""
        if self.driver:
            self.driver._recover()
        self.force_full_frame()

    def _resync_after_reinit_if_needed(self):
        """Force a full frame if a recovery was triggered since the last render.

        Called at the top of every render entry point (update_with_telemetry,
        render_crew_view) so that after _recover() runs in response to an SPI
        error, the next frame goes out as a full rewrite — guaranteeing the
        display matches _frame_buf again.
        """
        if self.driver and self.driver.reinit_occurred:
            self.driver.reinit_occurred = False
            self.force_full_frame()

    def display_region(self, x0: int, y0: int, x1: int, y1: int):
        """Send a rectangular region from _frame_buf_np to the display."""
        if self.driver:
            self.driver.display_region(x0, y0, x1, y1, self._frame_buf_np)

    def force_full_frame(self):
        """Reset partial-update state so the next frame is a full rewrite.

        Call after any display recovery or re-init to guarantee the display
        and _frame_buf are back in sync.
        """
        self._force_full_frame = True
        self._prev_frame_idx = None
        self._prev_marker_bbox = None

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

        # Cached HUD bytes: produced off-thread by HudComposer, consumed by the
        # render thread. The lock guards atomic-swap of bytes + version counters.
        # Per-bar versions let the render thread send only the bars that changed
        # (split-SPI-transfer strategy: ~5 ms per HUD bar vs ~51 ms full-frame).
        self._hud_lock = threading.Lock()
        self._hud_cache_key: Optional[str] = None
        self._hud_top_bytes: Optional[bytes] = None
        self._hud_bottom_bytes: Optional[bytes] = None
        self._hud_top_version: int = 0
        self._hud_bottom_version: int = 0

        # Composer wake interval (seconds). Lower = more responsive HUD digits;
        # higher = fewer split-SPI HUD writes (slightly smoother globe). HUD
        # updates no longer hitch the render loop, so this is a tuning knob
        # for HUD refresh rate, not a globe-smoothness knob.
        self._hud_min_render_interval = THEME.hud.min_render_interval_sec

        # ── Glyph atlases + RGB565 bar buffers (PIL-free HUD hot path) ──
        # One atlas per unique (font, color) pair; the NO LINK indicator
        # reuses the age element's atlas (same font/size, amber color).
        atlas_cache: dict = {}

        def atlas_for(font, color):
            key = (id(font), color)
            if key not in atlas_cache:
                atlas_cache[key] = self._build_glyph_atlas(font, color)
            return atlas_cache[key]

        for el in self._resolved.values():
            el.label.atlas = atlas_for(el.label.font, el.label.color)
            el.value.atlas = atlas_for(el.value.font, el.value.color)
            if el.unit is not None:
                el.unit.atlas = atlas_for(el.unit.font, el.unit.color)

        # NO LINK: OVER-cell font in the age (amber) color
        self._no_link_atlas = atlas_for(
            self._resolved["over"].value.font, self._resolved["age"].value.color
        )

        self._hud_bg565 = _rgb_to_rgb565(*self._hud_bg)
        self._hud_top_border565 = _rgb_to_rgb565(*self._hud_top_border)
        self._hud_bot_border565 = _rgb_to_rgb565(*self._hud_bot_border)

        # Composition buffers, owned by the single composer caller
        self._hud_top_buf = np.empty((self._hud_top_height, self.width), dtype='>u2')
        self._hud_bot_buf = np.empty((self._hud_bot_height, self.width), dtype='>u2')

    def _get_font(self, font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
        """Load a font at a given size, using the cache."""
        path = font_path or self._default_font_path
        if path is None:
            return ImageFont.load_default()
        key = (path, size)
        if key not in self._font_cache:
            self._font_cache[key] = ImageFont.truetype(path, size)
        return self._font_cache[key]

    def _build_glyph_atlas(self, font, color: RGB) -> dict:
        """Pre-render _ATLAS_CHARSET as RGB565 tiles on the HUD background.

        Each tile is one character rendered by PIL exactly as draw.text()
        would at a top-left anchor, so numpy-pasting tiles at cumulative
        advances is pixel-identical to the old full-string PIL rendering
        (the HUD font is monospace: no kerning). One-time startup cost so
        the per-second HUD recomposition never touches PIL again.
        """
        try:
            ascent, descent = font.getmetrics()
            tile_h = ascent + descent
        except AttributeError:  # bitmap fallback font
            tile_h = font.getbbox("Ag")[3] + 2
        atlas: dict = {}
        for ch in _ATLAS_CHARSET:
            adv = max(1, int(round(font.getlength(ch))))
            img = Image.new('RGB', (adv, tile_h), self._hud_bg)
            ImageDraw.Draw(img).text((0, 0), ch, fill=color, font=font)
            arr = np.array(img)
            r = arr[..., 0].astype(np.uint16)
            g = arr[..., 1].astype(np.uint16)
            b = arr[..., 2].astype(np.uint16)
            atlas[ch] = (((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)).astype('>u2')
        return atlas

    @staticmethod
    def _text_width(text: str, atlas: dict) -> int:
        return sum(atlas[ch].shape[1] for ch in text if ch in atlas)

    @staticmethod
    def _blit_text(buf: np.ndarray, x: int, y: int, text: str, atlas: dict) -> int:
        """Paste glyph tiles into a bar buffer. Returns the end x position."""
        H, W = buf.shape
        for ch in text:
            tile = atlas.get(ch)
            if tile is None:
                continue
            h, w = tile.shape
            if x >= W:
                break
            hc = min(h, H - y)
            wc = min(w, W - x)
            if hc > 0 and wc > 0:
                buf[y:y + hc, x:x + wc] = tile[:hc, :wc]
            x += w
        return x

    def render_hud_bytes(self, telemetry: Optional["ISSFix"]) -> Tuple[bytes, bytes, str]:
        """Compose the HUD bars from glyph atlases and return RGB565 bytes.

        Pure numpy tile pastes — no PIL in this path. The old PIL version
        held the GIL for ~30 ms per pass, which stalled the render thread
        mid-frame once per composer tick and showed up as a rhythmic hitch
        in the globe rotation. This version costs ~1 ms.

        Must only be called from one thread at a time (the composer; plus
        the single synchronous call at startup before the composer starts) —
        it composes into shared per-bar buffers. The returned bytes are
        copies and safe to hand across threads.

        telemetry is None before the first successful fetch: every value
        shows "--" and OVER shows NO LINK.

        Returns (top_bytes, bottom_bytes, cache_key).
        """
        if telemetry is None:
            lat = lon = 0.0
            lat_val = lon_val = alt_val = vel_val = age_val = "--"
            stale = True
        else:
            lat = telemetry.latitude
            lon = telemetry.longitude
            lat_dir = "N" if lat >= 0 else "S"
            lon_dir = "E" if lon >= 0 else "W"
            lat_val = f"{abs(lat):05.2f}\u00b0{lat_dir}"
            lon_val = f"{abs(lon):06.2f}\u00b0{lon_dir}"
            # Some sources (N2YO) omit altitude or velocity: show "--" rather
            # than a typical value dressed up as a measurement.
            alt_val = f"{telemetry.altitude_km:,.0f}" if telemetry.altitude_km else "--"
            vel_val = f"{telemetry.velocity_kmh:,.0f}" if telemetry.velocity_kmh else "--"
            age_val = _format_age(int(telemetry.data_age_sec))
            stale = telemetry.data_age_sec >= _STALE_DATA_SEC

        cache_key = f"{lat_val}|{lon_val}|{alt_val}|{vel_val}|{age_val}|{int(stale)}"

        w = self.width
        g = self._hud_grid
        label_y = self._hud_label_y
        value_y = self._hud_value_y

        # ── Top bar ──
        top = self._hud_top_buf
        top[:] = self._hud_bg565
        top[-1, :] = self._hud_top_border565

        # LAT cell
        lat_el = self._resolved["lat"]
        lat_x = g
        self._blit_text(top, lat_x, label_y, "LAT", lat_el.label.atlas)
        self._blit_text(top, lat_x, value_y, lat_val, lat_el.value.atlas)

        # LON cell
        lon_el = self._resolved["lon"]
        lon_x = lat_x + lat_el.cell_width + g
        self._blit_text(top, lon_x, label_y, "LON", lon_el.label.atlas)
        self._blit_text(top, lon_x, value_y, lon_val, lon_el.value.atlas)

        # Region indicator (right-aligned). During a prolonged outage the
        # position is a stale hold, so the region is unknown — flag NO LINK
        # in amber (Boeing caution color, shared with the LAST readout).
        over_el = self._resolved["over"]
        if stale:
            region = "NO LINK"
            region_atlas = self._no_link_atlas
        else:
            region = get_common_area_name(lat, lon)
            region_atlas = over_el.value.atlas
        right_edge = w - g
        over_label_w = self._text_width("OVER", over_el.label.atlas)
        self._blit_text(top, right_edge - over_label_w, label_y, "OVER", over_el.label.atlas)
        # Render multi-word regions with a tighter gap than the mono font's
        # full-width space (e.g. "N. America" → "N." + small gap + "America").
        words = region.split(" ")
        if len(words) > 1:
            space_w = self._text_width(" ", region_atlas)
            tight_gap = max(1, space_w // 3)
            word_widths = [self._text_width(w_, region_atlas) for w_ in words]
            total_w = sum(word_widths) + tight_gap * (len(words) - 1)
            x = right_edge - total_w
            for i, w_ in enumerate(words):
                self._blit_text(top, x, value_y, w_, region_atlas)
                x += word_widths[i] + tight_gap
        else:
            region_text_w = self._text_width(region, region_atlas)
            self._blit_text(top, right_edge - region_text_w, value_y, region, region_atlas)

        # ── Bottom bar ──
        bot = self._hud_bot_buf
        bot[:] = self._hud_bg565
        bot[0, :] = self._hud_bot_border565

        # ALT cell
        alt_el = self._resolved["alt"]
        alt_x = g
        self._blit_text(bot, alt_x, label_y, "ALT", alt_el.label.atlas)
        alt_end = self._blit_text(bot, alt_x, value_y, alt_val, alt_el.value.atlas)
        self._blit_text(bot, alt_end + self._hud_unit_gap,
                        value_y + alt_el.unit_baseline_offset, "km", alt_el.unit.atlas)

        # VEL cell
        vel_el = self._resolved["vel"]
        vel_x = alt_x + alt_el.cell_width + g
        self._blit_text(bot, vel_x, label_y, "VEL", vel_el.label.atlas)
        vel_end = self._blit_text(bot, vel_x, value_y, vel_val, vel_el.value.atlas)
        self._blit_text(bot, vel_end + self._hud_unit_gap,
                        value_y + vel_el.unit_baseline_offset, "km/h", vel_el.unit.atlas)

        # Data age indicator (right-aligned)
        age_el = self._resolved["age"]
        right_edge = w - g
        age_label_w = self._text_width("LAST", age_el.label.atlas)
        self._blit_text(bot, right_edge - age_label_w, label_y, "LAST", age_el.label.atlas)
        age_text_w = self._text_width(age_val, age_el.value.atlas)
        self._blit_text(bot, right_edge - age_text_w, value_y, age_val, age_el.value.atlas)

        return top.tobytes(), bot.tobytes(), cache_key

    def apply_hud_bytes(self, top_bytes: bytes, bottom_bytes: bytes, cache_key: str) -> None:
        """Atomically swap in newly-rendered HUD bytes (called from HudComposer).

        Increments per-bar version counters so the render thread knows to
        retransmit each bar on its next frame. No-op if the cache key is
        unchanged (the composer wakes on a fixed interval and may produce
        identical output between ticks).
        """
        with self._hud_lock:
            if cache_key == self._hud_cache_key:
                return
            self._hud_top_bytes = top_bytes
            self._hud_bottom_bytes = bottom_bytes
            self._hud_cache_key = cache_key
            self._hud_top_version += 1
            self._hud_bottom_version += 1

    # ─── Crew view ──────────────────────────────────────────────────────

    def _init_crew_view(self):
        """Pre-allocate resources for the People in Space view."""
        self._crew_img = Image.new('RGB', (self.width, self.height), (0, 0, 0))
        self._crew_cache_key: Optional[str] = None

    def invalidate_crew_cache(self):
        """Reset crew view cache so the next render_crew_view() redraws."""
        self._crew_cache_key = None

        # Pre-resolve fonts at sizes needed for crew view
        self._crew_title_font = self._get_font(None, 13)
        self._crew_stats_lbl_font = self._get_font(None, 10)
        self._crew_stats_val_font = self._get_font(None, 14)
        self._crew_header_font = self._get_font(None, 12)
        self._crew_list_font = self._get_font(None, 11)
        self._crew_footer_font = self._get_font(None, 9)
        self._crew_footer_val_font = self._get_font(None, 12)

    @staticmethod
    def _draw_dashed_line(draw, x0, x1, y, color, dash=4, gap=3):
        """Draw a horizontal dashed line."""
        x = x0
        while x < x1:
            end = min(x + dash, x1)
            draw.line([x, y, end, y], fill=color, width=1)
            x += dash + gap

    def _center_text(self, draw, text, y, font, color):
        """Draw text horizontally centered."""
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((self.width - tw) // 2, y), text, fill=color, font=font)

    def _draw_label_value_row(self, draw, labels, values,
                              lbl_font, val_font,
                              label_y, value_y, margin, color):
        """Draw label/value columns with equal gaps between edges.

        First column is anchored to the left margin and the last to the right;
        any columns in between are spaced so the gap between adjacent edges
        is uniform, which keeps wide and narrow columns from crowding.
        """
        W = self.width
        n = len(labels)
        if n == 0:
            return

        col_dims = []
        for lbl, val in zip(labels, values):
            lbl_w = draw.textbbox((0, 0), lbl, font=lbl_font)[2]
            val_w = draw.textbbox((0, 0), val, font=val_font)[2]
            col_dims.append((max(lbl_w, val_w), lbl_w, val_w))

        left_edge = margin + 2
        right_edge = W - margin - 2

        if n == 1:
            cxs = [(W - col_dims[0][0]) // 2]
        else:
            total_col_w = sum(c[0] for c in col_dims)
            gap = max(0.0, (right_edge - left_edge - total_col_w) / (n - 1))
            cxs = []
            x = float(left_edge)
            for col_w, _, _ in col_dims:
                cxs.append(int(round(x)))
                x += col_w + gap

        for i, ((col_w, lbl_w, val_w), lbl, val) in enumerate(zip(col_dims, labels, values)):
            cx = cxs[i]
            lx = cx + (col_w - lbl_w) // 2
            vx = cx + (col_w - val_w) // 2
            draw.text((lx, label_y), lbl, fill=color, font=lbl_font)
            draw.text((vx, value_y), val, fill=color, font=val_font)

    def render_crew_view(self, astros_data) -> bool:
        """Render the crew status monitor view into _frame_buf and send to display.

        Returns True if a frame was sent (data changed), False if cached.
        """
        self._resync_after_reinit_if_needed()
        key = f"{astros_data.count}|" + "|".join(
            f"{c.name}:{c.craft}" for c in astros_data.crew
        )
        if key == self._crew_cache_key:
            return False

        img = self._crew_img
        draw = ImageDraw.Draw(img)
        W = self.width
        color = (255, 255, 255)
        margin = 8

        # Clear to black
        draw.rectangle([0, 0, W, self.height], fill=(0, 0, 0))

        # ── Section 1: Title ──
        sp = 4  # spacing above/below lines
        self._center_text(draw, "HUMAN SPACEFLIGHT STATUS MONITOR",
                          6, self._crew_title_font, color)
        draw.line([margin, 26, W - margin, 26], fill=color, width=1)

        # ── Section 2: Status summary (label-over-value) ──
        num_craft = len(set(c.craft for c in astros_data.crew))
        stats_labels = ["CREW IN ORBIT", "ACTIVE CRAFT", "STATUS"]
        stats_values = [str(astros_data.count), str(num_craft), "NOMINAL"]
        self._draw_label_value_row(
            draw, stats_labels, stats_values,
            self._crew_stats_lbl_font, self._crew_stats_val_font,
            label_y=26 + sp + 2, value_y=26 + sp + 16, margin=margin, color=color)
        line2_y = 26 + sp + 16 + 18 + sp
        draw.line([margin, line2_y, W - margin, line2_y], fill=color, width=1)

        # ── Section 3: Column headers ──
        hdr_y = line2_y + sp + 2
        draw.text((margin + 2, hdr_y), "CREW MEMBER",
                  fill=color, font=self._crew_header_font)
        craft_hdr = "CRAFT"
        bbox = draw.textbbox((0, 0), craft_hdr, font=self._crew_header_font)
        craft_hdr_w = bbox[2] - bbox[0]
        draw.text((W - margin - 2 - craft_hdr_w, hdr_y), craft_hdr,
                  fill=color, font=self._crew_header_font)
        line3_y = hdr_y + 18 + sp
        draw.line([margin, line3_y, W - margin, line3_y], fill=color, width=1)

        # ── Section 4: Crew table ──
        # Group by craft, sort names alphabetically, ISS first
        crafts: dict[str, list[str]] = {}
        for member in astros_data.crew:
            crafts.setdefault(member.craft, []).append(member.name)
        for names in crafts.values():
            names.sort()
        craft_order = sorted(
            crafts.keys(),
            key=lambda c: (0 if c.upper() == "ISS" else 1, c.upper())
        )

        y = line3_y + sp + 2
        line_h = 22
        sep_h = 12
        bottom_zone = self.height - 40  # reserve space for footer

        # Flatten to rows first so overflow can be decided up front: a
        # dashed separator between craft groups (not before the first),
        # then one row per member.
        rows: list[Optional[tuple[str, str]]] = []  # None = separator
        for ci, craft_name in enumerate(craft_order):
            if ci > 0:
                rows.append(None)
            rows.extend((name.upper(), craft_name.upper())
                        for name in crafts[craft_name])

        # If everything doesn't fit, keep one row free for the "..." marker
        # so it stays above the footer.
        total_h = sum(sep_h if r is None else line_h for r in rows)
        limit = bottom_zone if y + total_h <= bottom_zone else bottom_zone - line_h

        for row in rows:
            row_h = sep_h if row is None else line_h
            if y + row_h > limit:
                draw.text((margin + 2, y), "...",
                          fill=color, font=self._crew_list_font)
                break
            if row is None:
                self._draw_dashed_line(draw, margin + 2, W - margin - 2,
                                       y, color)
            else:
                name, craft_label = row
                bbox = draw.textbbox((0, 0), craft_label, font=self._crew_list_font)
                craft_w = bbox[2] - bbox[0]
                draw.text((margin + 2, y), name,
                          fill=color, font=self._crew_list_font)
                draw.text((W - margin - 2 - craft_w, y), craft_label,
                          fill=color, font=self._crew_list_font)
            y += row_h

        # ── Section 5: Bottom status bar (labels + values) ──
        footer_line_y = self.height - 38
        draw.line([margin, footer_line_y, W - margin, footer_line_y],
                  fill=color, width=1)

        # Build per-craft footer items dynamically
        footer_labels = []
        footer_values = []
        for craft_name in craft_order:
            footer_labels.append(craft_name.upper())
            footer_values.append(str(len(crafts[craft_name])))
        footer_labels.append("MSG")
        footer_values.append("SUCCESS")

        self._draw_label_value_row(
            draw, footer_labels, footer_values,
            self._crew_footer_font, self._crew_footer_val_font,
            label_y=footer_line_y + sp, value_y=footer_line_y + sp + 14,
            margin=margin, color=color)

        # Convert to RGB565 and write to frame buffer
        rgb565_bytes = self._image_to_rgb565_bytes(img)
        self._frame_buf[:] = rgb565_bytes

        # Send to display
        if self.driver:
            self.driver.display_raw(self._frame_buf)
        else:
            self._preview_frame_count += 1
            self._save_preview(self._frame_buf)

        self._crew_cache_key = key
        return True

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

    def _rgb565_cache_path(self) -> Path:
        return self.cache_dir / f"globe_{self.num_frames}f_rgb565_{self.width}x{self.height}.npy"

    def _load_rgb565_cache(self) -> bool:
        """Load pre-converted RGB565 frames from disk, skipping PIL entirely.

        The conversion takes ~4.5 minutes on a Pi 3, which made every service
        restart a long blank-screen event; loading the persisted result takes
        seconds. Returns True on success.
        """
        path = self._rgb565_cache_path()
        if not path.exists():
            return False
        source = self.cache_dir / f"globe_{self.num_frames}f.npz"
        try:
            if source.exists() and source.stat().st_mtime > path.stat().st_mtime:
                logger.info("RGB565 cache older than globe frame cache, recomputing")
                return False
            stacked = np.load(path)
            expected = (self.num_frames, self.height, self.width)
            if stacked.dtype != np.dtype('>u2') or stacked.shape != expected:
                logger.warning(
                    "RGB565 cache mismatch (dtype=%s shape=%s, expected %s), recomputing",
                    stacked.dtype, stacked.shape, expected,
                )
                return False
            # Per-frame views into the single stacked array (no copies)
            self.frame_np_cache = [stacked[i] for i in range(self.num_frames)]
            logger.info("Loaded %d RGB565 frames from cache", self.num_frames)
            return True
        except Exception as e:
            logger.warning(f"Failed to load RGB565 cache: {e}, recomputing")
            return False

    def _precompute_rgb565(self):
        """Pre-compute RGB565 data for all cached frames and persist to disk.

        Produces big-endian numpy uint16 arrays (for np.copyto frame copies
        and partial-update region extraction). No-op when the frames were
        already loaded from the RGB565 disk cache.
        """
        if self.frame_np_cache:
            return
        logger.info("Pre-computing RGB565 frame data...")
        # Single stacked big-endian array: per-frame views share this one
        # backing buffer, and assignment into it converts from native order
        # exactly once. (np.stack must NOT be used here — it silently
        # produces native byte order, which would byte-swap on every frame
        # copy in the render hot path.)
        stacked = np.empty(
            (len(self.frame_cache), self.height, self.width), dtype='>u2'
        )
        for i, frame in enumerate(self.frame_cache):
            r = frame[..., 0].astype(np.uint16)
            g = frame[..., 1].astype(np.uint16)
            b = frame[..., 2].astype(np.uint16)
            stacked[i] = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            # Free each source frame as soon as it's converted: holding all of
            # them alongside the growing stacked array put the peak at
            # ~2.4 KB/px instead of ~1 KB/px, which at 480 frames would brush
            # the service's MemoryHigh and re-trigger reclaim thrash.
            self.frame_cache[i] = None
        self.frame_np_cache = [stacked[i] for i in range(len(self.frame_cache))]
        logger.info(f"Pre-computed {len(self.frame_np_cache)} frames")

        try:
            np.save(self._rgb565_cache_path(), stacked)
            logger.info("RGB565 cache saved: %s", self._rgb565_cache_path().name)
        except Exception as e:
            logger.warning(f"Failed to save RGB565 cache: {e}")

        # Free the source frames — render time only touches frame_np_cache,
        # and the RGB cache holds ~221 MB at 320x480 with 480 frames.
        self.frame_cache = []

    # ─── Frame cache ──────────────────────────────────────────────────────

    def _load_or_generate_frames(self):
        """Load pre-rendered frames from cache or generate them.

        Fast path: a valid RGB565 disk cache skips PIL frame loading and the
        multi-minute RGB565 conversion entirely.
        """
        if self._load_rgb565_cache():
            self.frames_generated = True
            self._update_globe_geometry()
            return

        cache_file = self.cache_dir / f"globe_{self.num_frames}f.npz"

        if cache_file.exists():
            logger.info("Loading cached Earth frames...")
            try:
                data = np.load(cache_file)
                for i in range(self.num_frames):
                    self.frame_cache.append(data[f'frame_{i}'])
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
            'grid_lat_spacing': g.grid_lat_spacing,
            'grid_lon_spacing': g.grid_lon_spacing,
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
                self.frame_cache.append(frame_array)
                if (i + 1) % 10 == 0 or (i + 1) == self.num_frames:
                    logger.info(f"  {i+1}/{self.num_frames} frames done")

        self._update_globe_geometry()

        # Save to cache (uncompressed — much faster to write than gzip).
        # The dict references the worker arrays directly: copying them here
        # doubled the cold-build peak to ~440 MB at 480 frames, past the
        # service's MemoryHigh.
        logger.info("Saving frames to cache...")
        try:
            frame_dict = {f'frame_{i}': frame
                          for i, frame in enumerate(self.frame_cache)}
            np.savez(self.cache_dir / f"globe_{self.num_frames}f.npz", **frame_dict)
            logger.info("Frames cached successfully")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")

        self.frames_generated = True
        logger.info("Frame generation complete!")

    def _update_globe_geometry(self):
        """Compute globe center and radius.

        Depends only on configured dimensions and globe_scale — matches the
        size the frames were rendered at, whichever cache they came from.
        """
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
                      alpha=globe_cfg['grid_alpha'], linestyle='-',
                      xlocs=np.arange(-180, 180, globe_cfg['grid_lon_spacing']),
                      ylocs=np.arange(-90, 91, globe_cfg['grid_lat_spacing']))
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

    def _draw_iss_marker_rgb565(self, px: int, py: int, opacity: float) -> Tuple[int, int, int, int]:
        """Draw ISS marker into self._frame_buf_np using NumPy vectorised operations.

        Draws concentric glow rings + core + center dot.
        Returns the (x0, y0, x1, y1) bounding box of the painted region so the
        caller can erase it on the next partial update.

        Uses pre-allocated arrays (_marker_dist_sq_full, _marker_color_buf,
        _marker_mask) to avoid per-frame numpy allocations that cause GC jitter.
        """
        m = THEME.marker
        size_scale = m.min_size_scale + (m.max_size_scale - m.min_size_scale) * opacity

        # Glow rings: list of (radius_squared, rgb565_color), outermost first
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

        center_b = int(m.center_color[0] * opacity)
        center_color = _rgb_to_rgb565(center_b, center_b, center_b)

        # Bounding box (clamped to screen)
        max_r = int(m.outer_ring_radius * size_scale) + 1
        x0 = max(0, px - max_r);  x1 = min(self.width - 1,  px + max_r)
        y0 = max(0, py - max_r);  y1 = min(self.height - 1, py + max_r)
        h_bb = y1 - y0 + 1
        w_bb = x1 - x0 + 1

        # Slice pre-computed distance-squared grid (no allocation)
        dy_start = (y0 - py) + self._marker_max_r
        dx_start = (x0 - px) + self._marker_max_r
        dist_sq = self._marker_dist_sq_full[dy_start:dy_start + h_bb, dx_start:dx_start + w_bb]

        # Reuse pre-allocated color buffer (zero the needed region)
        color_buf = self._marker_color_buf[:h_bb, :w_bb]
        color_buf[:] = 0

        # Paint outermost → innermost so inner shapes overwrite outer ones
        for ring_r_sq, ring_color in rings:
            color_buf[dist_sq <= ring_r_sq] = ring_color
        color_buf[dist_sq <= core_r * core_r] = core_color
        if center_b > 0:
            color_buf[dist_sq <= 1] = center_color

        # Write only non-zero pixels into the shared frame-buffer numpy view
        mask = self._marker_mask[:h_bb, :w_bb]
        np.not_equal(color_buf, 0, out=mask)
        self._frame_buf_np[y0:y1 + 1, x0:x1 + 1][mask] = color_buf[mask]

        return (x0, y0, x1, y1)

    def _patch_hud_bytes(self, frame_buf: bytearray):
        """Patch cached HUD bar bytes into a frame buffer.

        Snapshots both bars under the HUD lock so the composer can't tear
        the read by swapping bytes between the two assignments.
        """
        with self._hud_lock:
            top_bytes = self._hud_top_bytes
            bottom_bytes = self._hud_bottom_bytes
            top_version = self._hud_top_version
            bottom_version = self._hud_bottom_version
        if top_bytes is None or bottom_bytes is None:
            return

        top_size = self.width * self._hud_top_height * 2
        bot_size = self.width * self._hud_bot_height * 2
        bot_offset = (self.height - self._hud_bot_height) * self.width * 2

        # Top bar: rows 0..top_height
        frame_buf[0:top_size] = top_bytes

        # Bottom bar: rows (height - bot_height)..height
        frame_buf[bot_offset:bot_offset + bot_size] = bottom_bytes

        # Caller (full update) has just transmitted these bars in the same
        # full-frame SPI write, so mark them sent — otherwise the post-globe
        # HUD path would resend them on the next render.
        self._last_sent_top_version = top_version
        self._last_sent_bottom_version = bottom_version

    # ─── Main update loop entry point ─────────────────────────────────────

    def update_with_telemetry(self, telemetry: Optional["ISSFix"]):
        """Update the display with current ISS telemetry (None = no fix yet,
        drawn as the bare globe with no marker).

        Render-thread fast path. PIL HUD rendering happens off-thread in the
        HudComposer; this method only consumes the latest pre-rendered bytes
        and pushes them to SPI. Three transmission strategies, chosen per frame:

        Forced full update (init, view-toggle, error recovery):
          Compose entire frame in _frame_buf and send 307 KB in one transfer.

        Globe-region update (typical):
          Refresh the disc bbox in _frame_buf_np → draw marker → send the
          union of disc + marker bboxes (~17 ms SPI). When the composer has
          produced new HUD bytes since our last transmission, follow up with
          one or two HUD-bar region writes (~5 ms each). HUD updates therefore
          cost ~5–10 ms of extra SPI per frame, never block on PIL, and never
          force a full-frame transfer.

        Partial update (globe and HUD unchanged):
          Erase previous marker → draw new marker → send only the two tiny
          marker bounding-box regions (~1 KB total).
        """
        if not self.frames_generated:
            logger.warning("Frames not yet generated")
            return

        self._resync_after_reinit_if_needed()

        # Hybrid frame indexing: advance by one frame per render, but resync
        # to wall-clock if we've fallen too far behind (long stall recovery).
        # Eliminates visible angular skips when a render goes over budget.
        elapsed = time.monotonic() - self._rotation_start_time
        rotation_progress = (elapsed % self._rotation_period) / self._rotation_period
        target_frame = int(rotation_progress * self.num_frames) % self.num_frames
        prev_frame = self._prev_frame_idx
        if prev_frame is None:
            current_frame = target_frame
        else:
            delta = (target_frame - prev_frame) % self.num_frames
            if delta > _FRAME_RESYNC_THRESHOLD:
                current_frame = target_frame
            else:
                current_frame = (prev_frame + 1) % self.num_frames
        central_lon = (current_frame * (360.0 / self.num_frames)) - 180.0

        globe_changed = current_frame != self._prev_frame_idx
        iss_pos = (None if telemetry is None else
                   self._calc_iss_screen_pos(telemetry.latitude, telemetry.longitude, central_lon))

        if self._force_full_frame:
            # Forced resync: send everything in one transfer using whatever
            # HUD bytes the composer has produced. _do_full_update →
            # _patch_hud_bytes already updates _last_sent_*_version, so the
            # post-globe HUD path won't re-transmit immediately.
            self._do_full_update(current_frame, iss_pos)
            self._force_full_frame = False
        elif globe_changed:
            self._do_globe_region_update(current_frame, iss_pos)
            self._flush_hud_if_dirty()
        else:
            self._do_partial_update(current_frame, iss_pos)
            self._flush_hud_if_dirty()

        self._prev_frame_idx = current_frame

        # Preview mode: save occasional PNGs
        if self.driver is None:
            self._preview_frame_count += 1
            if self._preview_frame_count % 30 == 1:
                self._save_preview(self._frame_buf)

    def _flush_hud_if_dirty(self) -> None:
        """Transmit at most one stale HUD bar this frame.

        Spreads the cost of an HUD update across two consecutive frames
        instead of bundling both bars into one. With both bars sent on the
        same render iteration, the worst-case frame was globe-region (~35 ms)
        + 2 × HUD-bar transmission (~50 ms total) ≈ 85 ms. Sending one bar
        per frame caps that at ~45 ms, leaving every frame comfortably under
        the 58 ms budget at 17 FPS. The 1-frame tearing window between top
        and bottom updates is imperceptible.

        Top bar is prioritised so LAT/LON/OVER updates land first.
        """
        with self._hud_lock:
            top_version = self._hud_top_version
            bottom_version = self._hud_bottom_version
            top_bytes = self._hud_top_bytes
            bottom_bytes = self._hud_bottom_bytes

        if top_bytes is not None and top_version != self._last_sent_top_version:
            top_h = self._hud_top_height
            top_size = self.width * top_h * 2
            self._frame_buf[0:top_size] = top_bytes
            self.display_region(0, 0, self.width - 1, top_h - 1)
            self._last_sent_top_version = top_version
            return

        if bottom_bytes is not None and bottom_version != self._last_sent_bottom_version:
            bot_h = self._hud_bot_height
            bot_size = self.width * bot_h * 2
            bot_offset = (self.height - bot_h) * self.width * 2
            self._frame_buf[bot_offset:bot_offset + bot_size] = bottom_bytes
            self.display_region(0, self.height - bot_h, self.width - 1, self.height - 1)
            self._last_sent_bottom_version = bottom_version

    def _do_full_update(self, frame_idx: int, iss_pos):
        """Full-frame update: copy globe, draw marker, patch HUD, send everything."""
        np.copyto(self._frame_buf_np, self.frame_np_cache[frame_idx])

        new_bbox = None
        if iss_pos is not None:
            px, py, opacity = iss_pos
            new_bbox = self._draw_iss_marker_rgb565(px, py, opacity)

        self._patch_hud_bytes(self._frame_buf)

        if self.driver:
            self.driver.display_raw(self._frame_buf)

        self._prev_marker_bbox = new_bbox

    def _do_globe_region_update(self, frame_idx: int, iss_pos):
        """Globe changed but HUD didn't: send only the disc bbox + marker.

        Refreshes the globe disc into _frame_buf_np from the new cached frame,
        draws the marker, then sends a single SPI transfer covering the union
        of the disc bbox, the old marker bbox, and the new marker bbox. The
        HUD bytes are not retransmitted, dropping typical SPI cost from
        ~51 ms (full frame) to ~17 ms.
        """
        old_bbox = self._prev_marker_bbox

        # Restore old marker region from the cache. This is required when the
        # marker sat outside the disc bbox (it can extend past the disc by up
        # to (iss_orbit_scale - 1) * radius pixels). When inside the disc,
        # this is harmless extra work — the disc copy below overwrites it.
        if old_bbox is not None:
            x0, y0, x1, y1 = old_bbox
            self._frame_buf_np[y0:y1 + 1, x0:x1 + 1] = self.frame_np_cache[frame_idx][y0:y1 + 1, x0:x1 + 1]

        # Refresh the disc region from the new globe frame.
        dx0, dy0, dx1, dy1 = self._globe_disc_bbox
        self._frame_buf_np[dy0:dy1 + 1, dx0:dx1 + 1] = self.frame_np_cache[frame_idx][dy0:dy1 + 1, dx0:dx1 + 1]

        # Draw new marker.
        new_bbox = None
        if iss_pos is not None:
            px, py, opacity = iss_pos
            new_bbox = self._draw_iss_marker_rgb565(px, py, opacity)

        # Union(disc, old_marker, new_marker) sent as a single SPI transfer.
        ux0, uy0, ux1, uy1 = self._globe_disc_bbox
        for bb in (old_bbox, new_bbox):
            if bb is not None:
                ux0 = min(ux0, bb[0]); uy0 = min(uy0, bb[1])
                ux1 = max(ux1, bb[2]); uy1 = max(uy1, bb[3])

        self.display_region(ux0, uy0, ux1, uy1)
        self._prev_marker_bbox = new_bbox

    def _do_partial_update(self, frame_idx: int, iss_pos):
        """Partial update: erase old marker, draw new, send union bbox once.

        Uses a single SPI transfer covering both old and new marker regions
        to prevent flicker from the display briefly showing bare globe.
        """
        old_bbox = self._prev_marker_bbox

        # Erase old marker by restoring globe pixels (buffer only, no SPI)
        if old_bbox is not None:
            x0, y0, x1, y1 = old_bbox
            self._frame_buf_np[y0:y1 + 1, x0:x1 + 1] = self.frame_np_cache[frame_idx][y0:y1 + 1, x0:x1 + 1]

        # Draw new marker into buffer
        new_bbox = None
        if iss_pos is not None:
            px, py, opacity = iss_pos
            new_bbox = self._draw_iss_marker_rgb565(px, py, opacity)

        # Send the union of old and new bounding boxes in one SPI transfer
        union = old_bbox if new_bbox is None else new_bbox if old_bbox is None else (
            min(old_bbox[0], new_bbox[0]), min(old_bbox[1], new_bbox[1]),
            max(old_bbox[2], new_bbox[2]), max(old_bbox[3], new_bbox[3]),
        )
        if union is not None:
            self.display_region(*union)

        self._prev_marker_bbox = new_bbox

    def _save_preview(self, pixel_bytes: Union[bytes, bytearray]):
        """Save an RGB565 frame buffer as a PNG preview image."""
        try:
            arr = np.frombuffer(pixel_bytes, dtype='>u2').reshape(self.height, self.width)
            r = ((arr >> 11) & 0x1F).astype(np.uint8) * 8
            g = ((arr >> 5) & 0x3F).astype(np.uint8) * 4
            b = (arr & 0x1F).astype(np.uint8) * 8
            rgb = np.stack([r, g, b], axis=-1)
            img = Image.fromarray(rgb)
            slot = self._preview_saves % _MAX_PREVIEW_FILES
            self._preview_saves += 1
            preview_path = self.settings.preview_dir / f"frame_{slot:03d}.png"
            img.save(preview_path)
            logger.debug(f"Preview saved: {preview_path}")
        except Exception as e:
            logger.warning(f"Failed to save preview: {e}")

    def close(self):
        if self.driver:
            self.driver.close()
