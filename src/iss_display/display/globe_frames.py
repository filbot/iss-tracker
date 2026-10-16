"""Pre-rendered globe frames shipped with the package.

tools/render_globe.py draws the frames with Cartopy on a development machine
and saves them here; the device only loads them. Each frame holds just the
globe disc's bounding square, in the display's RGB565 big-endian format.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from iss_display.theme import THEME

FRAMES_PATH = Path(__file__).resolve().parent.parent / "assets" / "globe_frames.npz"

# Bump when the stored layout changes, so files in an older layout are rejected
_FORMAT = 1

_RENDER_HINT = ("Run `python tools/render_globe.py` on a development machine "
                "and commit the result.")


class GlobeFramesError(RuntimeError):
    """The globe frames are missing or don't match the current settings."""


def render_cfg() -> dict:
    """theme.toml globe settings that affect the rendered frames, as a plain
    picklable dict (passed to the render tool's worker processes)."""
    g = THEME.globe
    return {
        'map_resolution': g.map_resolution,
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


def disc_size(width: int, height: int) -> int:
    """Side of the globe disc's bounding square in pixels."""
    return int(min(width, height) * THEME.globe.scale)


def frames_key(width: int, height: int) -> str:
    """Hash of everything that changes the rendered frames, so frames from
    an edited theme.toml are rejected instead of shown stale."""
    key = {
        'format': _FORMAT,
        'globe': render_cfg(),
        'num_frames': THEME.globe.num_frames,
        'disc': disc_size(width, height),
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def load_frames(width: int, height: int, path: Path = FRAMES_PATH) -> np.ndarray:
    """Load the frames as a (num_frames, disc, disc) '>u2' array.

    Raises GlobeFramesError if the file is missing or was rendered for
    different globe settings or display size.
    """
    try:
        with np.load(path) as data:
            key = str(data['key'])
            frames = data['frames']
    except FileNotFoundError:
        raise GlobeFramesError(f"Globe frames not found at {path}. {_RENDER_HINT}") from None

    if key != frames_key(width, height):
        raise GlobeFramesError(
            f"{path.name} was rendered for different [globe] settings in "
            f"theme.toml or a different display size. {_RENDER_HINT}"
        )
    d = disc_size(width, height)
    expected = (THEME.globe.num_frames, d, d)
    if frames.dtype != np.dtype('>u2') or frames.shape != expected:
        raise GlobeFramesError(
            f"{path.name} holds {frames.shape} {frames.dtype}, expected "
            f"{expected} >u2. {_RENDER_HINT}"
        )
    return frames


def save_frames(frames: np.ndarray, width: int, height: int, path: Path = FRAMES_PATH) -> None:
    """Save frames with the current settings' key (temp file, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(f, frames=frames, key=np.array(frames_key(width, height)))
    os.replace(tmp, path)
