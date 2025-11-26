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
        # Manually download the essential kernels
        # naif0012.tls is the leapseconds kernel (LSK)
        # pck00010.tpc is the planetary constants kernel (PCK)
        # de430.bsp or de440.bsp is the planetary ephemeris (SPK) - we'll try a generic one if possible or rely on planetmapper defaults if LSK is present.
        
        # Note: The exact filenames on the NAIF server might change version numbers.
        # But planetmapper's download_kernel usually takes a URL or tries to find it?
        # Let's try to download specific URLs if we can, or use download_kernel with a guess.
        
        # Actually, let's try to just download the LSK first, which is the immediate error.
        # https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls
        
        lsk_url = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls"
        pck_url = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00010.tpc"
        
        logger.info(f"Downloading LSK from {lsk_url}...")
        planetmapper.kernel_downloader.download_file(lsk_url, str(kernel_dir / "naif0012.tls"))
        
        logger.info(f"Downloading PCK from {pck_url}...")
        planetmapper.kernel_downloader.download_file(pck_url, str(kernel_dir / "pck00010.tpc"))
        
        logger.info("Standard kernels downloaded successfully.")
        
    except Exception as e:
        logger.error(f"Error downloading kernels: {e}")
        raise

if __name__ == "__main__":
    setup_kernels()
