"""Visual theme constants for the ISS Tracker display.

Edit this file to control all visual styling — colors, sizes, fonts,
and layout — without touching the rendering code.

3-level cascade for HUD text styles:

    element override  >  bar base  >  hud base

Set any TextStyle field to None to inherit from the parent level.

Examples:
    # Make only the LAT label red
    lat = HudElement(label=TextStyle(color=(255, 0, 0)))

    # Make ALL top-bar labels cyan
    top = TopBarStyle(label=TextStyle(color=(0, 255, 255)))

    # Change the base label color for the entire HUD
    hud = HudStyle(label=TextStyle(color=(0, 200, 200), size=11))

Usage:
    from iss_display.theme import THEME
    color = THEME.hud.value.color
    bar_height = THEME.hud.top.height
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional, Tuple, Union, get_type_hints, get_origin, get_args

# Type alias for readability
RGB = Tuple[int, int, int]


def rgb_to_hex(color: RGB) -> str:
    """Convert an (R, G, B) tuple to a '#RRGGBB' hex string."""
    return f'#{color[0]:02x}{color[1]:02x}{color[2]:02x}'


# ── Globe ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GlobeStyle:
    """Controls the 3D Earth globe rendering (Cartopy orthographic projection)."""

    # Globe sizing
    scale: float = 0.70                    # Globe diameter as fraction of display short edge
    iss_orbit_scale: float = 1.10          # ISS altitude exaggeration (1.0 = on surface)
    num_frames: int = 144                  # Rotation frames (higher = smoother, more RAM/startup)
    rotation_period_sec: float = 14.0      # Seconds for one full rotation (tuned to match ~10.4 FPS display rate)

    # Colors (all RGB)
    background: RGB = (0, 0, 0)
    ocean_color: RGB = (10, 130, 209)
    land_color: RGB = (87, 32, 0)
    land_border_color: RGB = (64, 25, 3)
    land_border_width: float = 0.5
    coastline_color: RGB = (136, 136, 136)
    coastline_width: float = 0.5
    grid_color: RGB = (255, 255, 255)
    grid_width: float = 0.3
    grid_alpha: float = 0.5


# ── ISS Marker ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarkerStyle:
    """ISS position marker — the glowing dot on the globe."""

    # Colors
    glow_color: RGB = (255, 0, 0)          # Outer glow rings
    core_color: RGB = (255, 0, 0)          # Solid core
    center_color: RGB = (255, 255, 255)    # Center highlight

    # Ring geometry
    outer_ring_radius: int = 7             # Largest glow ring radius (px at full opacity)
    ring_step: int = 2                     # Radius reduction per ring
    ring_count: int = 3                    # Number of concentric glow rings
    core_radius: int = 3                   # Solid core radius (px at full opacity)

    # Ring brightness ramp (inner rings brighter)
    ring_brightness_base: int = 50         # Brightness of outermost ring (0-255)
    ring_brightness_step: int = 40         # Brightness increase per inner ring

    # Size scaling with opacity
    min_size_scale: float = 0.6            # Marker size at minimum visibility
    max_size_scale: float = 1.0            # Marker size at full visibility

    # Center dot
    center_dot_opacity_threshold: float = 0.5  # Show center dot only above this opacity

    # Visibility thresholds
    fade_start: float = 0.05              # cos_c below which fade begins
    opacity_cutoff: float = 0.05          # Below this, marker is hidden
    occlusion_factor: float = 0.3         # Opacity multiplier when behind Earth


# ── HUD Building Blocks ──────────────────────────────────────────────────


@dataclass(frozen=True)
class TextStyle:
    """Style for a single text element. None fields inherit from parent scope.

    Used at three cascade levels:
        1. HudStyle base    — the global defaults (should have no None fields)
        2. Bar base         — override for all elements in one bar
        3. HudElement       — override for one specific data field
    """
    color: Optional[RGB] = None
    size: Optional[int] = None
    font: Optional[str] = None             # Absolute path to a font file


@dataclass(frozen=True)
class HudElement:
    """Style overrides for one HUD data field (e.g., LAT, VEL).

    Any None value inherits from the owning bar's base style.
    """
    label: TextStyle = field(default_factory=TextStyle)
    value: TextStyle = field(default_factory=TextStyle)
    unit: TextStyle = field(default_factory=TextStyle)
    cell_width: Optional[int] = None       # None = right-aligned element


# ── Top Bar ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TopBarStyle:
    """Top HUD bar: LAT, LON, OVER.

    Bar-level base text styles override the HUD base.
    Per-element styles override the bar base.
    """

    # Bar layout
    height: int = 48
    border_color: Optional[RGB] = None     # None = inherit from HudStyle.border_color

    # Bar-level base text styles (override hud base when set)
    label: TextStyle = field(default_factory=TextStyle)
    value: TextStyle = field(default_factory=TextStyle)
    unit: TextStyle = field(default_factory=TextStyle)

    # Per-element overrides
    lat: HudElement = field(default_factory=lambda: HudElement(cell_width=85))
    lon: HudElement = field(default_factory=lambda: HudElement(cell_width=100))
    over: HudElement = field(default_factory=HudElement)


# ── Bottom Bar ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BottomBarStyle:
    """Bottom HUD bar: ALT, VEL, AGE.

    Bar-level base text styles override the HUD base.
    Per-element styles override the bar base.
    """

    # Bar layout
    height: int = 48
    border_color: Optional[RGB] = None     # None = inherit from HudStyle.border_color

    # Bar-level base text styles (override hud base when set)
    label: TextStyle = field(default_factory=TextStyle)
    value: TextStyle = field(default_factory=TextStyle)
    unit: TextStyle = field(default_factory=TextStyle)

    # Per-element overrides
    alt: HudElement = field(default_factory=lambda: HudElement(cell_width=85))
    vel: HudElement = field(default_factory=lambda: HudElement(cell_width=115))
    age: HudElement = field(default_factory=HudElement)


# ── HUD ──────────────────────────────────────────────────────────────────
#
# 3-level cascade for text styles (color, size, font):
#
#   element override  >  bar base  >  hud base
#
# Example: To make only the VEL label red:
#   bottom = BottomBarStyle(vel=HudElement(label=TextStyle(color=(255, 0, 0))))
#
# Example: To make ALL bottom-bar labels cyan:
#   bottom = BottomBarStyle(label=TextStyle(color=(0, 255, 255)))
#
# Example: To change the font just for the OVER region name:
#   top = TopBarStyle(over=HudElement(value=TextStyle(font="/path/to/serif.ttf")))


@dataclass(frozen=True)
class HudStyle:
    """Complete HUD styling — base defaults, bars, and font search paths."""

    # ── Global layout ──
    grid: int = 8                          # Base grid unit / horizontal padding (px)
    label_y: int = 6                       # Y offset for label text within bar
    value_y: int = 22                      # Y offset for value text within bar
    unit_gap: int = 2                      # Horizontal gap before unit suffix (px)
    background: RGB = (0, 0, 0)            # Bar background fill
    border_color: RGB = (255, 255, 255)    # Separator line color (default for both bars)

    # ── HUD-level base text styles (lowest priority in cascade) ──
    label: TextStyle = field(default_factory=lambda: TextStyle(
        color=(9, 222, 27),                # Green
        size=11,
    ))
    value: TextStyle = field(default_factory=lambda: TextStyle(
        color=(255, 255, 255),             # White
        size=17,
    ))
    unit: TextStyle = field(default_factory=lambda: TextStyle(
        color=(255, 255, 255),             # White
        size=15,
    ))

    # ── Font search paths (shared fallback list) ──
    # Individual TextStyle.font overrides bypass this search entirely.
    font_search_paths: Tuple[str, ...] = (
        "/usr/share/fonts/opentype/b612/B612Mono-Bold.otf",       # Airbus/ENAC cockpit font
        "/usr/share/fonts/opentype/b612/B612Mono-Regular.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
    )

    # ── Bars ──
    top: TopBarStyle = field(default_factory=TopBarStyle)
    bottom: BottomBarStyle = field(default_factory=BottomBarStyle)


# ── Cascade Resolution ───────────────────────────────────────────────────


def resolve_text_style(
    role: str,
    element: HudElement,
    bar: Union[TopBarStyle, BottomBarStyle],
    hud: HudStyle,
) -> TextStyle:
    """Resolve a text style through the 3-level cascade.

    Priority: element.{role} > bar.{role} > hud.{role}

    Uses ``is not None`` (not truthiness) so (0, 0, 0) and size 0 are valid.
    """
    el: TextStyle = getattr(element, role)
    bar_s: TextStyle = getattr(bar, role)
    base: TextStyle = getattr(hud, role)
    return TextStyle(
        color=el.color if el.color is not None else (bar_s.color if bar_s.color is not None else base.color),
        size=el.size if el.size is not None else (bar_s.size if bar_s.size is not None else base.size),
        font=el.font if el.font is not None else (bar_s.font if bar_s.font is not None else base.font),
    )


def resolve_border_color(
    bar: Union[TopBarStyle, BottomBarStyle],
    hud: HudStyle,
) -> RGB:
    """Resolve bar border color: bar override > hud default."""
    return bar.border_color if bar.border_color is not None else hud.border_color


# ── Top-level Theme ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Theme:
    """Top-level theme — the single entry point for all visual styling."""

    globe: GlobeStyle = field(default_factory=GlobeStyle)
    hud: HudStyle = field(default_factory=HudStyle)
    marker: MarkerStyle = field(default_factory=MarkerStyle)


# ── TOML Loader ──────────────────────────────────────────────────────────

_logger = logging.getLogger(__name__)


def _get_nested_type(cls: type, field_name: str):
    """Return the dataclass type for a nested field, or None."""
    try:
        hints = get_type_hints(cls)
    except Exception:
        return None
    hint = hints.get(field_name)
    if hint is None:
        return None
    # Unwrap Optional[X] → X
    if get_origin(hint) is Union:
        args = [a for a in get_args(hint) if a is not type(None)]
        if len(args) == 1:
            hint = args[0]
    if isinstance(hint, type) and hasattr(hint, '__dataclass_fields__'):
        return hint
    return None


def _build(cls: type, data: dict, base=None):
    """Build a frozen dataclass by merging TOML *data* over a *base* instance.

    Fields present in *data* override the base; missing fields keep the base
    value.  Nested dicts are recursed into using the base's value for that
    field, so parent-level defaults (like cell_width on HudElement) are
    preserved even when only a child key is set in TOML.
    """
    if base is None:
        base = cls()
    kwargs = {}
    known = {f.name for f in fields(cls)}
    for f in fields(cls):
        if f.name in data:
            val = data[f.name]
            if isinstance(val, dict):
                nested_cls = _get_nested_type(cls, f.name)
                if nested_cls is not None:
                    kwargs[f.name] = _build(nested_cls, val, base=getattr(base, f.name))
                else:
                    kwargs[f.name] = val
            elif isinstance(val, list):
                kwargs[f.name] = tuple(val)
            else:
                kwargs[f.name] = val
        else:
            kwargs[f.name] = getattr(base, f.name)
    # Warn about unknown keys
    for key in data:
        if key not in known and not isinstance(data[key], dict):
            _logger.warning("Unknown theme key '%s' in [%s], skipping", key, cls.__name__)
    return cls(**kwargs)


def _find_theme_toml() -> Optional[Path]:
    """Walk up from this file to find theme.toml."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        candidate = parent / "theme.toml"
        if candidate.is_file():
            return candidate
    return None


def _load_theme() -> Theme:
    """Load theme from theme.toml, falling back to built-in defaults."""
    path = _find_theme_toml()
    if path is None:
        _logger.info("No theme.toml found, using built-in defaults")
        return Theme()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        theme = _build(Theme, data)
        _logger.info("Loaded theme from %s", path)
        return theme
    except Exception as exc:
        _logger.warning("Failed to load theme.toml: %s — using built-in defaults", exc)
        return Theme()


# ── Module-level singleton ────────────────────────────────────────────────
# Import this in rendering code:
#   from iss_display.theme import THEME

THEME = _load_theme()
