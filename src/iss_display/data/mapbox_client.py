"""Straightforward Mapbox Static Images client."""

from __future__ import annotations

import io
from typing import Optional

import requests
from PIL import Image

from iss_display.config import Settings


class MapboxClient:
    """Requests a single portrait image centered on the ISS location."""

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None) -> None:
        if not settings.mapbox_token:
            raise RuntimeError("MAPBOX_TOKEN is required to download map imagery")
        self._settings = settings
        self._session = session or requests.Session()

    def get_portrait_image(self, lat: float, lon: float) -> Image.Image:
        url = self._build_static_image_url(lat, lon)
        response = self._session.get(url, timeout=30)
        response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as downloaded:
            image = downloaded.convert("RGB").copy()

        target_size = (self._settings.display_logical_width, self._settings.display_height)
        if image.size != target_size:
            image = image.resize(target_size, Image.LANCZOS)
        return image

    def _build_static_image_url(self, lat: float, lon: float) -> str:
        pin_color = self._settings.pin_color.lstrip("#") or "ED1C24"
        width = self._settings.display_logical_width
        height = self._settings.display_height
        zoom = self._settings.mapbox_zoom
        return (
            "https://api.mapbox.com/styles/v1/"
            f"{self._settings.mapbox_username}/{self._settings.mapbox_style_id}/static/"
            f"pin-s+{pin_color}({lon:.6f},{lat:.6f})/"
            f"{lon:.6f},{lat:.6f},{zoom},0/"
            f"{width}x{height}?access_token={self._settings.mapbox_token}&logo=false&attribution=false"
        )


__all__ = ["MapboxClient"]
