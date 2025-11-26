import planetmapper
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def setup_kernels():
    # Define the kernel directory relative to the project root
    # assuming this script is run from project root
    kernel_dir = Path("var/spice_kernels").resolve()
    kernel_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Setting PlanetMapper kernel path to: {kernel_dir}")
    planetmapper.set_kernel_path(str(kernel_dir))

    logger.info("Downloading standard SPICE kernels...")
    try:
        # download_standard_kernels downloads:
        # - naif0012.tls (Leap seconds)
        # - pck00010.tpc (Planetary constants)
        # - de430.bsp (Planetary ephemeris) - Wait, de430 is huge. 
        # planetmapper might download a smaller one or de440s.
        planetmapper.kernel_downloader.download_standard_kernels()
        logger.info("Standard kernels downloaded successfully.")
    except AttributeError:
        # Fallback if the method name is different in this version
        logger.warning("planetmapper.kernel_downloader.download_standard_kernels() not found.")
        logger.info("Attempting to initialize Body to trigger download...")
        try:
            planetmapper.Body('earth', '2023-01-01')
        except Exception as e:
            logger.error(f"Failed to trigger download via Body init: {e}")
    except Exception as e:
        logger.error(f"Error downloading kernels: {e}")
        raise

if __name__ == "__main__":
    setup_kernels()
