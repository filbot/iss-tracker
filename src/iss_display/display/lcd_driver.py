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
        self.data(0x48) # MY=0, MX=1, MV=0, ML=0, BGR=1, MH=0
        
        # Display Inversion On (some displays need INVOFF instead)
        self.command(INVON)
        
        # Power Control and other settings can be default for now
        
        self.command(NORON)
        time.sleep(0.01)
        
        self.command(DISPON)
        time.sleep(0.01)
        
        # Turn on backlight
        GPIO.output(self.bl, GPIO.HIGH)
        
        # Fill screen with red to verify display is working
        self._test_fill(0xF800)  # Red in RGB565
        time.sleep(0.5)
    
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
            
        # Convert to RGB565
        # ST7796S expects 16-bit color (5 bits red, 6 bits green, 5 bits blue)
        # We can do this with numpy for speed
        
        img_np = np.array(image) # (H, W, 3) RGB
        
        # Extract channels
        r = img_np[..., 0]
        g = img_np[..., 1]
        b = img_np[..., 2]
        
        # Convert to 565
        # R: 5 bits (mask 0xF8) -> shift right 3 -> shift left 11
        # G: 6 bits (mask 0xFC) -> shift right 2 -> shift left 5
        # B: 5 bits (mask 0xF8) -> shift right 3
        
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        
        # Flatten and convert to bytes (Big Endian)
        # High byte first
        high_byte = (rgb565 >> 8).astype(np.uint8)
        low_byte = (rgb565 & 0xFF).astype(np.uint8)
        
        # Interleave
        # We need a flat list of [H, L, H, L, ...]
        # Stack them
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
        # Use a local directory 'var/spice_kernels' to ensure we have write access and it's self-contained
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
            # Attempt to re-raise or handle gracefully?
            # If we can't load the body, we can't render the wireframe.
            # We could fallback to a simple circle?
            raise e

    def update(self, lat: float, lon: float):
        """
        Updates the display with the current ISS position.
        """
        # Create Matplotlib Figure
        dpi = 100
        fig = plt.figure(figsize=(self.width/dpi, self.height/dpi), dpi=dpi)
        
        # Set background color
        fig.patch.set_facecolor(self.settings.ui_background_color)
        
        ax = fig.add_axes([0, 0, 1, 1]) # Full screen axes
        ax.set_facecolor(self.settings.ui_background_color)
        ax.axis('off') # Hide axes
        
        try:
            iss_ra, iss_dec = self.body.lonlat2radec(lon, lat)
        except Exception:
            iss_ra, iss_dec = self.body.target_ra, self.body.target_dec

        # Plot Wireframe
        self.body.plot_wireframe_angular(
            ax,
            origin_ra=iss_ra,
            origin_dec=iss_dec,
            scale_factor=None,
            color=self.settings.ui_earth_color,
            grid_interval=30,
            formatting={
                'grid': {'linestyle': '-', 'linewidth': 0.5, 'alpha': 0.5, 'color': self.settings.ui_earth_color},
                'limb': {'linewidth': 1, 'color': self.settings.ui_earth_color},
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
                ang_x, ang_y = self.body.radec2angular(ras, decs, origin_ra=iss_ra, origin_dec=iss_dec)
                ax.plot(ang_x, ang_y, color=self.settings.ui_earth_color, linewidth=1)
            except Exception:
                continue

        # Plot ISS Marker (Centered)
        ax.plot(0, 0, 'o', color=self.settings.ui_iss_color, markersize=5)
        
        # Save to buffer
        canvas = agg.FigureCanvasAgg(fig)
        canvas.draw()
        
        rgba_buffer = canvas.buffer_rgba()
        size = canvas.get_width_height()
        
        self.image = Image.frombuffer("RGBA", size, rgba_buffer)
        self.image = self.image.convert("RGB")
        
        plt.close(fig)
        
        # Send to Hardware
        if self.driver:
            logger.info("Sending image to hardware display...")
            self.driver.display(self.image)
            logger.info("Image sent to hardware display")
        else:
            logger.warning("No hardware driver available, skipping display update")
        
        # Save preview
        if self.settings.preview_dir:
            preview_path = self.settings.preview_dir / "lcd_preview.png"
            self.image.save(preview_path)
            logger.info(f"Saved preview to {preview_path}")

    def close(self):
        if self.driver:
            self.driver.close()
