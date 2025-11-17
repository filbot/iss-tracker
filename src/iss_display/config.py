"""Minimal configuration loader for the ISS display application."""

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
    mapbox_token: str
    mapbox_username: str
    mapbox_style_id: str
    mapbox_zoom: int
    pin_color: str
    iss_api_url: str
    display_width: int
    display_height: int
    display_has_red: bool
    display_logical_width: int
    display_pad_left: int
    display_pad_right: int
    preview_dir: Path
    preview_only: bool
    log_level: str

    @classmethod
    def load(cls) -> "Settings":
        preview_dir = Path(os.getenv("ISS_PREVIEW_DIR", "var/previews")).resolve()
        preview_dir.mkdir(parents=True, exist_ok=True)

        token = os.getenv("MAPBOX_TOKEN")
        if not token:
            raise RuntimeError("MAPBOX_TOKEN is required")

        return cls(
            mapbox_token=token,
            mapbox_username=os.getenv("MAPBOX_USERNAME", "mapbox"),
            mapbox_style_id=os.getenv("MAPBOX_STYLE_ID", "streets-v12"),
            mapbox_zoom=int(os.getenv("MAPBOX_ZOOM", "2")),
            pin_color=os.getenv("MAP_PIN_COLOR", "#ED1C24"),
            iss_api_url=os.getenv("ISS_API_URL", "https://api.wheretheiss.at/v1/satellites/25544"),
            display_width=int(os.getenv("EPD_WIDTH", "128")),
            display_height=int(os.getenv("EPD_HEIGHT", "250")),
            display_has_red=_as_bool(os.getenv("EPD_HAS_RED", "false"), default=False),
            display_logical_width=int(os.getenv("EPD_LOGICAL_WIDTH", "122")),
            display_pad_left=int(os.getenv("EPD_PAD_LEFT", "3")),
            display_pad_right=int(os.getenv("EPD_PAD_RIGHT", "3")),
            preview_dir=preview_dir,
            preview_only=_as_bool(os.getenv("EPD_PREVIEW_ONLY", "false"), default=False),
            log_level=os.getenv("ISS_LOG_LEVEL", "INFO"),
        )

