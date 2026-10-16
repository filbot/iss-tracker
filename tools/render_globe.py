#!/usr/bin/env python3
"""Render the globe frames with Cartopy and save them into the package.

Run this on a development machine after changing any [globe] setting in
theme.toml (or the display size), then commit
src/iss_display/assets/globe_frames.npz. The device only loads that file.

Needs the render extras:
    pip install -e '.[render]'
    python tools/render_globe.py
"""

from __future__ import annotations

import multiprocessing as mp
import time

import numpy as np

from iss_display.config import Settings
from iss_display.display.globe_frames import (
    FRAMES_PATH, disc_size, frames_key, render_cfg, save_frames,
)
from iss_display.display.lcd_driver import _rgb_array_to_rgb565
from iss_display.theme import THEME, rgb_to_hex


def render_frame(args: tuple) -> np.ndarray:
    """Render one orthographic globe frame as a (size, size, 3) uint8 RGB array."""
    central_lon, size, cfg = args

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature

    res = cfg['map_resolution']
    bg_hex = rgb_to_hex(cfg['background'])
    dpi = 100
    fig = plt.figure(figsize=(size / dpi, size / dpi), dpi=dpi, facecolor=bg_hex)

    projection = ccrs.Orthographic(central_longitude=central_lon, central_latitude=0)
    ax = fig.add_subplot(1, 1, 1, projection=projection)
    ax.set_facecolor(bg_hex)
    ax.set_global()

    ax.add_feature(cfeature.NaturalEarthFeature(
        'physical', 'ocean', res,
        facecolor=rgb_to_hex(cfg['ocean_color']), edgecolor='none'), zorder=0)
    ax.add_feature(cfeature.NaturalEarthFeature(
        'physical', 'land', res,
        facecolor=rgb_to_hex(cfg['land_color']),
        edgecolor=rgb_to_hex(cfg['land_border_color']),
        linewidth=cfg['land_border_width']), zorder=1)
    ax.add_feature(cfeature.NaturalEarthFeature(
        'physical', 'coastline', res,
        facecolor='none',
        edgecolor=rgb_to_hex(cfg['coastline_color']),
        linewidth=cfg['coastline_width']), zorder=2)
    ax.gridlines(color=rgb_to_hex(cfg['grid_color']),
                 linewidth=cfg['grid_width'],
                 alpha=cfg['grid_alpha'], linestyle='-',
                 xlocs=np.arange(-180, 180, cfg['grid_lon_spacing']),
                 ylocs=np.arange(-90, 91, cfg['grid_lat_spacing']))
    ax.spines['geo'].set_visible(False)

    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

    fig.canvas.draw()
    rgb = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
    plt.close(fig)
    if rgb.shape != (size, size, 3):
        raise RuntimeError(f"rendered {rgb.shape}, expected {(size, size, 3)}")
    return rgb


def main() -> None:
    settings = Settings.load()
    width, height = settings.display_width, settings.display_height
    num_frames = THEME.globe.num_frames
    size = disc_size(width, height)
    cfg = render_cfg()

    work = [(i * 360 / num_frames - 180, size, cfg) for i in range(num_frames)]
    frames = np.empty((num_frames, size, size), dtype='>u2')

    print(f"Rendering {num_frames} frames, {size}x{size} px, "
          f"Natural Earth {cfg['map_resolution']}, {mp.cpu_count()} workers")
    start = time.monotonic()
    with mp.Pool(mp.cpu_count()) as pool:
        for i, rgb in enumerate(pool.imap(render_frame, work)):
            frames[i] = _rgb_array_to_rgb565(rgb)
            if (i + 1) % 40 == 0 or i + 1 == num_frames:
                print(f"  {i + 1}/{num_frames}")

    save_frames(frames, width, height)
    print(f"Saved {FRAMES_PATH} ({FRAMES_PATH.stat().st_size / 1e6:.1f} MB, "
          f"key {frames_key(width, height)}) in {time.monotonic() - start:.0f}s")


if __name__ == "__main__":
    main()
