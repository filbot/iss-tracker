"""Visual theme constants for the ISS Tracker display.

Edit this file to control all visual styling — colors, sizes, fonts,
and layout — without touching the rendering code.

Usage:
    from iss_display.theme import THEME
    color = THEME.hud_colors.primary
    font_size = THEME.hud_typography.value_size
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

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
    rotation_period_sec: float = 10.0      # Seconds for one full rotation (independent of num_frames)

    # Colors (all RGB)
    background: RGB = (0, 0, 0)
    ocean_color: RGB = (0, 17, 51)
    land_color: RGB = (255, 255, 255)
    land_border_color: RGB = (204, 204, 204)
    land_border_width: float = 0.5
    coastline_color: RGB = (136, 136, 136)
    coastline_width: float = 0.5
    grid_color: RGB = (68, 68, 68)
    grid_width: float = 0.3
    grid_alpha: float = 0.5


# ── HUD ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HudColors:
    """HUD color palette — NASA amber theme."""

    primary: RGB = (52, 219, 235)           # Main readout values
    label: RGB = (255, 225, 31)            # Field labels (LAT, LON, etc.)
    dim: RGB = (255, 225, 31)               # Unit suffixes (km, km/h)
    border: RGB = (60, 50, 15)             # Separator lines
    background: RGB = (0, 0, 0)         # Bar background
    indicator: RGB = (80, 200, 100)        # ISS online dot


@dataclass(frozen=True)
class HudTypography:
    """HUD font sizes and search paths."""

    value_size: int = 20                   # Primary telemetry numbers
    unit_size: int = 15                    # Unit labels (km, km/h)
    label_size: int = 11                   # Field names (LAT, LON, ALT, VEL)

    # Monospace fonts to try, in order of preference
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


@dataclass(frozen=True)
class HudLayout:
    """HUD spatial layout — pixel dimensions and grid spacing."""

    grid: int = 8                          # Base grid unit (px)
    top_bar_height: int = 48               # Top HUD bar height
    bottom_bar_height: int = 48            # Bottom HUD bar height

    # Vertical positioning within bars (Y coordinates)
    label_y: int = 6                       # Y for label text
    value_y: int = 22                      # Y for value text
    unit_gap: int = 2                      # Gap between value and unit suffix (px)

    # Cell widths for telemetry fields
    lat_cell_width: int = 95
    lon_cell_width: int = 105
    iss_cell_width: int = 45
    alt_cell_width: int = 85
    vel_cell_width: int = 115
    orb_cell_width: int = 50

    # ISS status indicator
    indicator_dot_radius: int = 4


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


# ── Top-level Theme ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Theme:
    """Top-level theme — the single entry point for all visual styling."""

    globe: GlobeStyle = field(default_factory=GlobeStyle)
    hud_colors: HudColors = field(default_factory=HudColors)
    hud_typography: HudTypography = field(default_factory=HudTypography)
    hud_layout: HudLayout = field(default_factory=HudLayout)
    marker: MarkerStyle = field(default_factory=MarkerStyle)


# ── Module-level singleton ────────────────────────────────────────────────
# Import this in rendering code:
#   from iss_display.theme import THEME

THEME = Theme()
