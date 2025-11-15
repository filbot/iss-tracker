"""Application configuration and settings management."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    """Runtime settings loaded from environment variables."""

    mapbox_token: Optional[str]
    mapbox_username: str
    mapbox_style_id: str
    mapbox_zoom: int
    mapbox_tile_size: int
    mapbox_hidpi: bool
    mapbox_bearing: float
    mapbox_refresh_radius_km: float
    mapbox_max_requests_per_hour: int
    mapbox_enable_static_fallback: bool
    portrait_work_width: int
    portrait_height: int
    portrait_trim_left: int
    portrait_trim_right: int
    pin_color: str
    iss_api_url: str
    iss_poll_interval: int
    display_width: int
    display_height: int
    display_has_red: bool
    display_logical_width: int
    display_pad_left: int
    display_pad_right: int
    led_enabled: bool
    led_pin: int
    cache_dir: Path
    state_dir: Path
    preview_dir: Path
    preview_only: bool
    log_level: str
    force_refresh: bool

    @classmethod
    def load(cls) -> "Settings":
        """Load settings from the process environment."""

        cache_dir = Path(os.getenv("ISS_CACHE_DIR", "var/cache")).resolve()
        state_dir = Path(os.getenv("ISS_STATE_DIR", "var/state")).resolve()
        preview_dir = Path(os.getenv("ISS_PREVIEW_DIR", "var/previews")).resolve()
        for directory in (cache_dir, state_dir, preview_dir):
            directory.mkdir(parents=True, exist_ok=True)

        return cls(
            mapbox_token=os.getenv("MAPBOX_TOKEN"),
            mapbox_username=os.getenv("MAPBOX_USERNAME", "your-mapbox-username"),
            mapbox_style_id=os.getenv("MAPBOX_STYLE_ID", "your-style-id"),
            mapbox_zoom=int(os.getenv("MAPBOX_ZOOM", "2")),
            mapbox_tile_size=int(os.getenv("MAPBOX_TILE_SIZE", "512")),
            mapbox_hidpi=os.getenv("MAPBOX_HIDPI", "true").lower() in {"1", "true", "yes"},
            mapbox_bearing=float(os.getenv("MAPBOX_BEARING", "0")),
            mapbox_refresh_radius_km=float(os.getenv("MAPBOX_REFRESH_RADIUS_KM", "400")),
            mapbox_max_requests_per_hour=int(os.getenv("MAPBOX_MAX_REQUESTS_PER_HOUR", "60")),
            mapbox_enable_static_fallback=os.getenv("MAPBOX_ENABLE_STATIC_FALLBACK", "true").lower()
            in {"1", "true", "yes"},
            portrait_work_width=int(os.getenv("PORTRAIT_WORK_WIDTH", "128")),
            portrait_height=int(os.getenv("PORTRAIT_HEIGHT", "250")),
            portrait_trim_left=int(os.getenv("PORTRAIT_TRIM_LEFT", "3")),
            portrait_trim_right=int(os.getenv("PORTRAIT_TRIM_RIGHT", "3")),
            pin_color=os.getenv("MAP_PIN_COLOR", "#ED1C24"),
            iss_api_url=os.getenv("ISS_API_URL", "https://api.wheretheiss.at/v1/satellites/25544"),
            iss_poll_interval=int(os.getenv("ISS_POLL_INTERVAL", "10")),
            display_width=int(os.getenv("EPD_WIDTH", "128")),
            display_height=int(os.getenv("EPD_HEIGHT", "250")),
            display_has_red=os.getenv("EPD_HAS_RED", "true").lower() in {"1", "true", "yes"},
            display_logical_width=int(os.getenv("EPD_LOGICAL_WIDTH", "122")),
            display_pad_left=int(os.getenv("EPD_PAD_LEFT", "3")),
            display_pad_right=int(os.getenv("EPD_PAD_RIGHT", "3")),
            led_enabled=os.getenv("LED_ENABLED", "false").lower() in {"1", "true", "yes"},
            led_pin=int(os.getenv("LED_PIN", "12")),
            cache_dir=cache_dir,
            state_dir=state_dir,
            preview_dir=preview_dir,
            preview_only=os.getenv("EPD_PREVIEW_ONLY", "false").lower() in {"1", "true", "yes"},
            log_level=os.getenv("ISS_LOG_LEVEL", "INFO"),
            force_refresh=os.getenv("ISS_FORCE_REFRESH", "false").lower() in {"1", "true", "yes"},
        )


SETTINGS = Settings.load()
