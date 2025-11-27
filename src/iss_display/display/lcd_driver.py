import logging
import time
from pathlib import Path
from typing import Optional, List, Tuple
import datetime

from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.backends.backend_agg as agg
import planetmapper
import numpy as np

try:
    import spidev
    import RPi.GPIO as GPIO
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False

from iss_display.config import Settings
from iss_display.data.world_110m import LAND_MASSES

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
        self.command(MADCTL)
        self.data(0x48)  # MY=0, MX=1, MV=0, ML=0, BGR=1, MH=0 (BGR order)
        
        # Display Inversion On (some displays need INVOFF instead)
        self.command(INVON)
        
        # Power Control and other settings can be default for now
        
        self.command(NORON)
        time.sleep(0.01)
        
        self.command(DISPON)
        time.sleep(0.01)
        
        # Turn on backlight
        GPIO.output(self.bl, GPIO.HIGH)
    
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
            
        # Convert to RGB565 for ST7796S (BGR order)
        img_np = np.array(image)  # (H, W, 3) RGB
        
        # Extract channels - swap R and B for BGR display
        r = img_np[..., 2]  # Use blue channel as red
        g = img_np[..., 1]
        b = img_np[..., 0]  # Use red channel as blue
        
        # Convert to 565
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        
        # Flatten and convert to bytes (Big Endian)
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
        
        # Configure PlanetMapper kernel path
        self.kernel_dir = self.settings.preview_dir.parent / "spice_kernels"
        self.kernel_dir.mkdir(parents=True, exist_ok=True)
        planetmapper.set_kernel_path(str(self.kernel_dir))
        
        # Initialize PlanetMapper Body
        try:
            self.body = planetmapper.Body('earth', datetime.datetime.now(), observer='moon')
        except Exception as e:
            logger.warning(f"Failed to initialize PlanetMapper Body: {e}")
            logger.info("This is likely due to missing SPICE kernels.")
            logger.info(f"Please check {self.kernel_dir} or run a setup script to download them.")
            raise e
        
        # Pre-rendered frame cache
        self.frame_cache: List[Image.Image] = []
        self.num_frames = 72  # 72 frames = 5 degrees per frame for full rotation
        self.current_frame = 0
        self.frames_generated = False
        
        # Cache directory
        self.cache_dir = self.settings.preview_dir.parent / "frame_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Try to load cached frames or generate them
        self._load_or_generate_frames()

    def _load_or_generate_frames(self):
        """Load pre-rendered frames from cache or generate them."""
        cache_file = self.cache_dir / "frames.npz"
        
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
        """Pre-render all Earth rotation frames."""
        logger.info(f"Generating {self.num_frames} Earth frames (this may take a minute)...")
        
        self.frame_cache = []
        degrees_per_frame = 360 / self.num_frames
        
        for i in range(self.num_frames):
            rotation = i * degrees_per_frame
            logger.info(f"  Generating frame {i+1}/{self.num_frames} ({rotation:.0f}°)...")
            
            frame = self._render_earth_frame(rotation)
            self.frame_cache.append(frame)
        
        # Save to cache
        logger.info("Saving frames to cache...")
        try:
            frame_dict = {f'frame_{i}': np.array(frame) for i, frame in enumerate(self.frame_cache)}
            np.savez_compressed(self.cache_dir / "frames.npz", **frame_dict)
            logger.info("Frames cached successfully")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
        
        self.frames_generated = True
        logger.info("Frame generation complete!")

    def _render_earth_frame(self, rotation_degrees: float) -> Image.Image:
        """Render a single Earth frame at the given rotation."""
        dpi = 100
        fig = plt.figure(figsize=(self.width/dpi, self.height/dpi), dpi=dpi)
        fig.patch.set_facecolor(self.settings.ui_background_color)
        
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor(self.settings.ui_background_color)
        ax.axis('off')
        
        # Calculate view based on rotation
        view_lon = rotation_degrees
        if view_lon > 180:
            view_lon -= 360
        
        try:
            view_ra, view_dec = self.body.lonlat2radec(view_lon, 0)
        except Exception:
            view_ra, view_dec = self.body.target_ra, self.body.target_dec

        # Plot Wireframe Earth
        self.body.plot_wireframe_angular(
            ax,
            origin_ra=view_ra,
            origin_dec=view_dec,
            scale_factor=None,
            color=self.settings.ui_earth_color,
            grid_interval=30,
            formatting={
                'grid': {'linestyle': '-', 'linewidth': 0.5, 'alpha': 0.5, 'color': self.settings.ui_earth_color},
                'limb': {'linewidth': 2, 'color': self.settings.ui_earth_color},
                'terminator': {'visible': False},
                'prime_meridian': {'visible': False},
                'equator': {'visible': False},
            }
        )
        
        # Plot Land Masses
        for poly in LAND_MASSES:
            lats = [p[0] for p in poly]
            lons = [p[1] for p in poly]
            
            try:
                ras, decs = self.body.lonlat2radec(np.array(lons), np.array(lats))
                ang_x, ang_y = self.body.radec2angular(ras, decs, origin_ra=view_ra, origin_dec=view_dec)
                ax.plot(ang_x, ang_y, color=self.settings.ui_earth_color, linewidth=1)
            except Exception:
                continue
        
        # Store view info for ISS overlay
        ax._view_ra = view_ra
        ax._view_dec = view_dec
        ax._rotation = rotation_degrees
        
        canvas = agg.FigureCanvasAgg(fig)
        canvas.draw()
        
        rgba_buffer = canvas.buffer_rgba()
        size = canvas.get_width_height()
        
        image = Image.frombuffer("RGBA", size, rgba_buffer)
        image = image.convert("RGB")
        
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
        
        # Calculate ISS position on this frame
        rotation = self.current_frame * (360 / self.num_frames)
        
        # Add ISS marker overlay
        self._add_iss_marker(base_frame, lat, lon, rotation)
        
        self.image = base_frame
        
        # Advance to next frame
        self.current_frame = (self.current_frame + 1) % self.num_frames
        
        # Send to Hardware
        if self.driver:
            self.driver.display(self.image)
        
        # Save preview (only occasionally to reduce disk I/O)
        if self.settings.preview_dir and self.current_frame % 10 == 0:
            preview_path = self.settings.preview_dir / "lcd_preview.png"
            self.image.save(preview_path)

    def _add_iss_marker(self, image: Image.Image, lat: float, lon: float, rotation: float):
        """Draw ISS marker on the frame at the correct position."""
        from PIL import ImageDraw
        
        # Calculate where ISS appears on this rotation
        view_lon = rotation
        if view_lon > 180:
            view_lon -= 360
        
        try:
            view_ra, view_dec = self.body.lonlat2radec(view_lon, 0)
            iss_ra, iss_dec = self.body.lonlat2radec(lon, lat)
            
            # Get angular position
            iss_x, iss_y = self.body.radec2angular(iss_ra, iss_dec, origin_ra=view_ra, origin_dec=view_dec)
            
            # Convert angular position to pixel coordinates
            # The angular coordinates are typically in degrees, centered at 0,0
            # We need to map them to image pixels
            cx, cy = self.width // 2, self.height // 2
            
            # Scale factor - approximate based on typical angular size of Earth
            # Earth appears about 2 degrees across from Moon distance
            scale = min(self.width, self.height) / 4  # Adjust this based on actual view
            
            px = int(cx + iss_x * scale)
            py = int(cy - iss_y * scale)  # Flip Y for image coordinates
            
            # Check if ISS is on visible side (roughly)
            # If angular distance from center is too large, ISS is on far side
            angular_dist = np.sqrt(iss_x**2 + iss_y**2)
            if angular_dist > 1.2:  # Earth radius in angular units
                return  # ISS is on far side, don't draw
            
            # Check bounds
            if 0 <= px < self.width and 0 <= py < self.height:
                draw = ImageDraw.Draw(image)
                
                # Draw glow
                r_glow = 12
                for i in range(3):
                    alpha = 80 - i * 25
                    r = r_glow - i * 3
                    draw.ellipse([px-r, py-r, px+r, py+r], fill=(255, 50, 50))
                
                # Draw solid marker
                r_marker = 6
                draw.ellipse([px-r_marker, py-r_marker, px+r_marker, py+r_marker], fill=(255, 0, 0))
                
        except Exception as e:
            logger.debug(f"Could not plot ISS marker: {e}")

    def close(self):
        if self.driver:
            self.driver.close()
