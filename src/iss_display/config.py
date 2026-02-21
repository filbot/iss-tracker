"""Configuration loader for the ISS display application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str, *, default: bool = False) -> bool:
    truthy = {"1", "true", "yes", "on"}
    falsy = {"0", "false", "no", "off"}
    text = value.strip().lower()
    if text in truthy:
        return True
    if text in falsy:
        return False
    return default


@dataclass(frozen=True)
class Settings:
    iss_api_url: str
    n2yo_api_key: str
    display_width: int
    display_height: int
    preview_dir: Path
    preview_only: bool
    log_level: str
    gpio_dc: int
    gpio_rst: int
    gpio_bl: int
    spi_bus: int
    spi_device: int
    spi_speed_hz: int

    @classmethod
    def load(cls) -> "Settings":
        preview_dir = Path(os.getenv("ISS_PREVIEW_DIR", "var/previews")).resolve()
        preview_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            iss_api_url=os.getenv("ISS_API_URL", "https://api.wheretheiss.at/v1/satellites/25544"),
            n2yo_api_key=os.getenv("N2YO_API_KEY", ""),
            display_width=int(os.getenv("DISPLAY_WIDTH", "320")),
            display_height=int(os.getenv("DISPLAY_HEIGHT", "480")),
            preview_dir=preview_dir,
            preview_only=_as_bool(os.getenv("PREVIEW_ONLY", "false"), default=False),
            log_level=os.getenv("ISS_LOG_LEVEL", "INFO"),
            gpio_dc=int(os.getenv("GPIO_DC", "22")),
            gpio_rst=int(os.getenv("GPIO_RST", "27")),
            gpio_bl=int(os.getenv("GPIO_BL", "18")),
            spi_bus=int(os.getenv("SPI_BUS", "0")),
            spi_device=int(os.getenv("SPI_DEVICE", "0")),
            spi_speed_hz=int(os.getenv("SPI_SPEED_HZ", "48000000")),
        )
