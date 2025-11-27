#!/usr/bin/env python3
"""
Generate frame cache on a fast machine (Mac/PC) and copy to Raspberry Pi.

Usage:
    python generate_frames.py

This will create var/frame_cache/cartopy_frames_v2.npz which you can copy to the Pi:
    scp var/frame_cache/cartopy_frames_v2.npz pi@<pi-ip>:~/iss-tracker/var/frame_cache/
"""

import io
import logging
import os
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
except ImportError:
    print("ERROR: Cartopy is not installed. Run: pip install cartopy")
    exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Display settings - MUST match Pi config!
# Default matches config.py defaults (portrait mode 320x480)
DISPLAY_WIDTH = int(os.getenv("DISPLAY_WIDTH", "320"))
DISPLAY_HEIGHT = int(os.getenv("DISPLAY_HEIGHT", "480"))
NUM_FRAMES = 72  # 5 degrees per frame
GLOBE_SCALE = 0.70


def render_globe_frame(central_lon: float, central_lat: float = 0) -> Image.Image:
    """Render a single globe frame at the given central longitude."""
    globe_size = int(min(DISPLAY_WIDTH, DISPLAY_HEIGHT) * GLOBE_SCALE)
    dpi = 100
    fig = plt.figure(figsize=(globe_size/dpi, globe_size/dpi), dpi=dpi, facecolor='black')
    
    projection = ccrs.Orthographic(central_longitude=central_lon, central_latitude=central_lat)
    
    ax = fig.add_subplot(1, 1, 1, projection=projection)
    ax.set_facecolor('black')
    ax.set_global()
    
    # Add features
    ax.add_feature(cfeature.OCEAN, facecolor='#001133', edgecolor='none', zorder=0)
    ax.add_feature(cfeature.LAND, facecolor='#FFFFFF', edgecolor='#CCCCCC', linewidth=0.5, zorder=1)
    ax.add_feature(cfeature.COASTLINE, edgecolor='#888888', linewidth=0.5, zorder=2)
    ax.gridlines(color='#444444', linewidth=0.3, alpha=0.5, linestyle='-')
    
    ax.spines['geo'].set_visible(False)
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    
    # Render to image
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, facecolor='black', edgecolor='none',
                bbox_inches='tight', pad_inches=0)
    buf.seek(0)
    
    globe_img = Image.open(buf).convert('RGB')
    plt.close(fig)
    
    # Create final image centered on display
    final_img = Image.new('RGB', (DISPLAY_WIDTH, DISPLAY_HEIGHT), (0, 0, 0))
    x_offset = (DISPLAY_WIDTH - globe_img.width) // 2
    y_offset = (DISPLAY_HEIGHT - globe_img.height) // 2
    final_img.paste(globe_img, (x_offset, y_offset))
    
    return final_img


def main():
    cache_dir = Path("var/frame_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "cartopy_frames_v2.npz"
    
    logger.info(f"Generating {NUM_FRAMES} Earth frames...")
    logger.info(f"Display size: {DISPLAY_WIDTH}x{DISPLAY_HEIGHT}")
    logger.info(f"Globe scale: {GLOBE_SCALE}")
    
    frames = []
    degrees_per_frame = 360 / NUM_FRAMES
    
    for i in range(NUM_FRAMES):
        central_lon = (i * degrees_per_frame) - 180
        logger.info(f"  Frame {i+1}/{NUM_FRAMES} (lon={central_lon:.0f}°)")
        
        frame = render_globe_frame(central_lon)
        frames.append(np.array(frame))
    
    # Save to cache
    logger.info(f"Saving to {cache_file}...")
    frame_dict = {f'frame_{i}': frame for i, frame in enumerate(frames)}
    np.savez_compressed(cache_file, **frame_dict)
    
    file_size_mb = cache_file.stat().st_size / (1024 * 1024)
    logger.info(f"Done! Cache file size: {file_size_mb:.1f} MB")
    logger.info("")
    logger.info("To copy to Raspberry Pi:")
    logger.info(f"  scp {cache_file} pi@<raspberry-pi-ip>:~/iss-tracker/var/frame_cache/")


if __name__ == "__main__":
    main()
