import logging
import time
from pathlib import Path
from typing import Optional, List
import io

from PIL import Image
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import numpy as np

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

logger = logging.getLogger(__name__)

# ST7796S Command Constants
SWRESET = 0x01
SLPOUT  = 0x11
NORON   = 0x13
INVOFF  = 0x20
INVON   = 0x21
DISPOFF = 0x28
DISPON  = 0x29
CASET   = 0x2A
RASET   = 0x2B
RAMWR   = 0x2C
MADCTL  = 0x36
COLMOD  = 0x3A

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
        GPIO.output(self.bl, GPIO.LOW) # Backlight off initially

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
        
    def data_list(self, data: List[int]):
        GPIO.output(self.dc, GPIO.HIGH)
        # Split into chunks if too large (standard spidev limit is often 4096)
        chunk_size = 4096
        for i in range(0, len(data), chunk_size):
            self.spi.writebytes(data[i:i+chunk_size])

    def _init_display(self):
        self._reset()
        
        self.command(SWRESET)
        time.sleep(0.15)
        
        self.command(SLPOUT)
        time.sleep(0.15)
        
        # Interface Pixel Format
        self.command(COLMOD)
        self.data(0x55) # 16-bit/pixel
        
        # Memory Access Control
        # Bits: MY MX MV ML BGR MH 0 0
        # 0x48 = 0100 1000 = MX=1, BGR=1
        # BGR=1 is needed because ST7796S panels typically have BGR subpixel order
        self.command(MADCTL)
        self.data(0x48)
        
        # Display Inversion On (some displays need INVOFF instead)
        self.command(INVON)
        
        # Power Control and other settings can be default for now
        
        self.command(NORON)
        time.sleep(0.01)
        
        self.command(DISPON)
        time.sleep(0.01)
        
        # Turn on backlight
        GPIO.output(self.bl, GPIO.HIGH)
        
        # Clear screen to black
        self._test_fill(0x0000)
    
    def _test_fill(self, color: int):
        """Fill the entire screen with a solid color (RGB565)."""
        self.set_window(0, 0, self.width - 1, self.height - 1)
        
        high = (color >> 8) & 0xFF
        low = color & 0xFF
        
        # Create pixel data for entire screen
        total_pixels = self.width * self.height
        pixel_data = [high, low] * total_pixels
        
        GPIO.output(self.dc, GPIO.HIGH)
        
        chunk_size = 4096
        for i in range(0, len(pixel_data), chunk_size):
            self.spi.writebytes(pixel_data[i:i+chunk_size])

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

    def display(self, image: Image.Image):
        if image.width != self.width or image.height != self.height:
            image = image.resize((self.width, self.height))
            
        # Convert to RGB565 - standard format
        # RGB565: RRRRR GGGGGG BBBBB (16 bits total)
        img_np = np.array(image)  # (H, W, 3) RGB as uint8
        
        # CRITICAL: Must cast to uint16 BEFORE shifting, otherwise numpy uint8 overflows!
        r = img_np[..., 0].astype(np.uint16)
        g = img_np[..., 1].astype(np.uint16)
        b = img_np[..., 2].astype(np.uint16)
        
        # Standard RGB565 format
        # Red:   5 bits in positions 15-11
        # Green: 6 bits in positions 10-5
        # Blue:  5 bits in positions 4-0
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        
        # Split into bytes (Big Endian for ST7796S)
        high_byte = (rgb565 >> 8).astype(np.uint8)
        low_byte = (rgb565 & 0xFF).astype(np.uint8)
        
        pixel_data = np.dstack((high_byte, low_byte)).flatten().tolist()
        
        self.set_window(0, 0, self.width - 1, self.height - 1)
        
        GPIO.output(self.dc, GPIO.HIGH)
        
        # Write in chunks
        chunk_size = 4096
        for i in range(0, len(pixel_data), chunk_size):
            self.spi.writebytes(pixel_data[i:i+chunk_size])

    def close(self):
        GPIO.cleanup()
        self.spi.close()


class LcdDisplay:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.width = settings.display_width
        self.height = settings.display_height
        
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
                logger.warning("Hardware libraries (spidev/RPi.GPIO) not found. Running in preview mode.")
            else:
                logger.info("Running in preview-only mode")
        
        if not CARTOPY_AVAILABLE:
            logger.error("Cartopy is not installed. Please run: pip install cartopy")
            raise ImportError("Cartopy is required for rendering the globe")
        
        # Pre-rendered frame cache
        self.frame_cache: List[Image.Image] = []
        self.num_frames = 72  # 72 frames = 5 degrees per frame for full rotation
        self.current_frame = 0
        self.frames_generated = False
        
        # Cache directory
        self.cache_dir = self.settings.preview_dir.parent / "frame_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Current image
        self.image: Optional[Image.Image] = None
        
        # Try to load cached frames or generate them
        self._load_or_generate_frames()

    def _load_or_generate_frames(self):
        """Load pre-rendered frames from cache or generate them."""
        cache_file = self.cache_dir / "cartopy_frames.npz"
        
        if cache_file.exists():
            logger.info("Loading cached Earth frames...")
            try:
                data = np.load(cache_file)
                for i in range(self.num_frames):
                    img_array = data[f'frame_{i}']
                    self.frame_cache.append(Image.fromarray(img_array))
                self.frames_generated = True
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
            central_lon = (i * degrees_per_frame) - 180  # Range: -180 to 180
            logger.info(f"  Generating frame {i+1}/{self.num_frames} (lon={central_lon:.0f}°)...")
            
            frame = self._render_globe_frame(central_lon)
            self.frame_cache.append(frame)
        
        # Save to cache
        logger.info("Saving frames to cache...")
        try:
            frame_dict = {f'frame_{i}': np.array(frame) for i, frame in enumerate(self.frame_cache)}
            np.savez_compressed(self.cache_dir / "cartopy_frames.npz", **frame_dict)
            logger.info("Frames cached successfully")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
        
        self.frames_generated = True
        logger.info("Frame generation complete!")

    def _render_globe_frame(self, central_lon: float, central_lat: float = 0) -> Image.Image:
        """Render a single globe frame at the given central longitude."""
        # Create figure with appropriate size
        dpi = 100
        fig = plt.figure(figsize=(self.width/dpi, self.height/dpi), dpi=dpi, facecolor='black')
        
        # Create Orthographic projection centered on the given longitude
        projection = ccrs.Orthographic(central_longitude=central_lon, central_latitude=central_lat)
        
        ax = fig.add_subplot(1, 1, 1, projection=projection)
        ax.set_facecolor('black')
        
        # Set the global extent
        ax.set_global()
        
        # Add ocean (dark blue background for the globe)
        ax.add_feature(cfeature.OCEAN, facecolor='#001133', edgecolor='none', zorder=0)
        
        # Add land masses (white/light color)
        ax.add_feature(cfeature.LAND, facecolor='#FFFFFF', edgecolor='#CCCCCC', linewidth=0.5, zorder=1)
        
        # Add coastlines for better definition
        ax.add_feature(cfeature.COASTLINE, edgecolor='#888888', linewidth=0.5, zorder=2)
        
        # Add gridlines
        ax.gridlines(color='#444444', linewidth=0.3, alpha=0.5, linestyle='-')
        
        # Remove axis spines/border (compatible with newer Cartopy versions)
        ax.spines['geo'].set_visible(False)
        
        # Remove all margins
        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
        
        # Render to image
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=dpi, facecolor='black', edgecolor='none',
                    bbox_inches='tight', pad_inches=0)
        buf.seek(0)
        
        image = Image.open(buf).convert('RGB')
        
        # Resize to exact display dimensions if needed
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.Resampling.LANCZOS)
        
        plt.close(fig)
        return image

    def update(self, lat: float, lon: float):
        """
        Updates the display with the current ISS position on rotating Earth.
        Uses pre-rendered frames for smooth animation.
        """
        if not self.frames_generated:
            logger.warning("Frames not yet generated")
            return
        
        # Get current frame
        base_frame = self.frame_cache[self.current_frame].copy()
        
        # Calculate the central longitude of this frame
        central_lon = (self.current_frame * (360 / self.num_frames)) - 180
        
        # Add ISS marker overlay
        self._add_iss_marker(base_frame, lat, lon, central_lon)
        
        self.image = base_frame
        
        # Advance to next frame
        self.current_frame = (self.current_frame + 1) % self.num_frames
        
        # Send to Hardware
        if self.driver:
            self.driver.display(self.image)
        
        # Save preview
        if self.settings.preview_dir:
            preview_path = self.settings.preview_dir / "lcd_preview.png"
            self.image.save(preview_path)

    def _add_iss_marker(self, image: Image.Image, lat: float, lon: float, central_lon: float):
        """Draw ISS marker on the frame at the correct position using Cartopy projection math."""
        from PIL import ImageDraw
        
        # Calculate if ISS is visible from this view angle
        # The ISS is visible if it's within ~90 degrees of the central longitude
        lon_diff = abs(lon - central_lon)
        if lon_diff > 180:
            lon_diff = 360 - lon_diff
        
        # Also check latitude visibility (within ~90 degrees from equator for our 0° central_lat view)
        if lon_diff > 90:
            # ISS is on the far side of the globe
            return
        
        # Project ISS position to screen coordinates using orthographic projection math
        # Orthographic projection formulas:
        # x = R * cos(lat) * sin(lon - central_lon)
        # y = R * cos(central_lat) * sin(lat) - sin(central_lat) * cos(lat) * cos(lon - central_lon)
        # For central_lat = 0:
        # x = R * cos(lat) * sin(lon - central_lon)
        # y = R * sin(lat)
        
        import math
        
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        central_lon_rad = math.radians(central_lon)
        central_lat_rad = 0  # We're viewing from equator
        
        # Orthographic projection
        x = math.cos(lat_rad) * math.sin(lon_rad - central_lon_rad)
        y = math.cos(central_lat_rad) * math.sin(lat_rad) - math.sin(central_lat_rad) * math.cos(lat_rad) * math.cos(lon_rad - central_lon_rad)
        
        # Check if point is on visible hemisphere
        # cos(c) = sin(central_lat) * sin(lat) + cos(central_lat) * cos(lat) * cos(lon - central_lon)
        cos_c = math.sin(central_lat_rad) * math.sin(lat_rad) + math.cos(central_lat_rad) * math.cos(lat_rad) * math.cos(lon_rad - central_lon_rad)
        
        if cos_c < 0:
            # Point is on far side
            return
        
        # Convert to pixel coordinates
        # x and y are in range [-1, 1] representing the globe
        # The globe typically fills about 80% of the smaller dimension
        globe_radius = min(self.width, self.height) * 0.4
        cx, cy = self.width // 2, self.height // 2
        
        px = int(cx + x * globe_radius)
        py = int(cy - y * globe_radius)  # Flip Y for image coordinates
        
        # Check bounds
        if 0 <= px < self.width and 0 <= py < self.height:
            draw = ImageDraw.Draw(image)
            
            # Draw glow effect (red)
            for i in range(3):
                r = 10 - i * 3
                color = (255, 50 + i * 30, 50 + i * 30)
                draw.ellipse([px-r, py-r, px+r, py+r], fill=color)
            
            # Draw solid red marker
            r_marker = 5
            draw.ellipse([px-r_marker, py-r_marker, px+r_marker, py+r_marker], fill=(255, 0, 0))
            
            # Draw white center dot
            r_center = 2
            draw.ellipse([px-r_center, py-r_center, px+r_center, py+r_center], fill=(255, 255, 255))

    def close(self):
        if self.driver:
            self.driver.close()
