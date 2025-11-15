"""Mapbox Static Tiles client with portrait cropping, caching, and fallback."""

from __future__ import annotations

import io
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from iss_display.config import Settings

LOGGER = logging.getLogger(__name__)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two coordinates in kilometers."""

    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


@dataclass(frozen=True)
class TileAddress:
    z: int
    x: int
    y: int
    pixel_x: float
    pixel_y: float
    scale: int


class MapboxClient:
    """Fetches static tiles and produces portrait-oriented map imagery."""

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None) -> None:
        if not settings.mapbox_token:
            raise RuntimeError("MAPBOX_TOKEN is required to download map imagery")

        self._settings = settings
        self._session = session or requests.Session()
        self._tile_cache_dir = settings.cache_dir / "tiles" / settings.mapbox_style_id / str(settings.mapbox_zoom)
        self._portrait_cache_dir = settings.cache_dir / "portraits" / settings.mapbox_style_id / str(settings.mapbox_zoom)
        self._state_file = settings.state_dir / "mapbox.json"
        self._tile_cache_dir.mkdir(parents=True, exist_ok=True)
        self._portrait_cache_dir.mkdir(parents=True, exist_ok=True)

        self._state = {
            "last_lat": None,
            "last_lon": None,
            "hour_started": 0,
            "requests_this_hour": 0,
            "current_portrait_path": None,
        }
        self._load_state()

    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        if self._state_file.exists():
            persisted = json.loads(self._state_file.read_text())
            self._state.update(persisted)

    def _persist_state(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._state, indent=2))

    # ------------------------------------------------------------------
    def needs_refresh(self, lat: float, lon: float, *, force: bool = False) -> bool:
        if force:
            return True
        last_lat = self._state.get("last_lat")
        last_lon = self._state.get("last_lon")
        if last_lat is None or last_lon is None:
            return True
        distance = haversine_km(lat, lon, last_lat, last_lon)
        return distance >= self._settings.mapbox_refresh_radius_km

    def get_portrait_image(self, lat: float, lon: float, *, force: bool = False) -> Image.Image:
        if not force and not self.needs_refresh(lat, lon):
            cached_path = self._state.get("current_portrait_path")
            if cached_path and Path(cached_path).exists():
                with Image.open(cached_path) as cached:
                    image = cached.convert("RGB").copy()
                self._record_portrait_usage(lat, lon, Path(cached_path))
                return image

        address = self._tile_address(lat, lon)
        portrait_cache = self._portrait_cache_path(address)
        if portrait_cache.exists() and not force:
            with Image.open(portrait_cache) as cached:
                image = cached.convert("RGB").copy()
            self._record_portrait_usage(lat, lon, portrait_cache)
            return image

        try:
            tile_image = self._load_tile(address, force=force)
            portrait = self._render_portrait(tile_image, address)
        except Exception as exc:  # pragma: no cover - logged fallback path
            if not self._settings.mapbox_enable_static_fallback:
                raise
            LOGGER.warning("Tile fetch failed (%s); falling back to legacy static image", exc)
            portrait = self._fallback_static_portrait(lat, lon)
        portrait_cache.parent.mkdir(parents=True, exist_ok=True)
        portrait.save(portrait_cache, format="PNG")
        self._record_portrait_usage(lat, lon, portrait_cache)
        return portrait

    # ------------------------------------------------------------------
    def _tile_address(self, lat: float, lon: float) -> TileAddress:
        zoom = self._settings.mapbox_zoom
        n = 2 ** zoom
        tile_size = self._settings.mapbox_tile_size
        scale = 2 if self._settings.mapbox_hidpi else 1

        x_float = (lon + 180.0) / 360.0 * n
        y_float = (1.0 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n

        tile_x = int(math.floor(x_float))
        tile_y = int(math.floor(y_float))

        pixel_x = (x_float - tile_x) * tile_size * scale
        pixel_y = (y_float - tile_y) * tile_size * scale

        tile_dim = tile_size * scale
        pixel_x = max(0.0, min(tile_dim - 1.0, pixel_x))
        pixel_y = max(0.0, min(tile_dim - 1.0, pixel_y))

        return TileAddress(z=zoom, x=tile_x, y=tile_y, pixel_x=pixel_x, pixel_y=pixel_y, scale=scale)

    def _tile_url(self, address: TileAddress) -> str:
        suffix = "@2x" if self._settings.mapbox_hidpi else ""
        return (
            f"https://api.mapbox.com/styles/v1/{self._settings.mapbox_username}/{self._settings.mapbox_style_id}/tiles/"
            f"{self._settings.mapbox_tile_size}/{address.z}/{address.x}/{address.y}{suffix}?access_token={self._settings.mapbox_token}"
        )

    def _tile_cache_path(self, address: TileAddress) -> Path:
        suffix = "@2x" if self._settings.mapbox_hidpi else ""
        filename = f"{address.x}_{address.y}{suffix}.png"
        return self._tile_cache_dir / filename

    def _portrait_cache_path(self, address: TileAddress) -> Path:
        key = f"{address.x}_{address.y}_{int(address.pixel_x)}_{int(address.pixel_y)}.png"
        return self._portrait_cache_dir / key

    def _load_tile(self, address: TileAddress, *, force: bool) -> Image.Image:
        cache_path = self._tile_cache_path(address)
        if cache_path.exists() and not force:
            with Image.open(cache_path) as cached:
                return cached.convert("RGB").copy()

        self._enforce_quota()
        url = self._tile_url(address)
        response = self._session.get(url, timeout=30)
        response.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)

        with Image.open(io.BytesIO(response.content)) as tile:
            image = tile.convert("RGB").copy()

        self._state["requests_this_hour"] = self._state.get("requests_this_hour", 0) + 1
        self._persist_state()
        return image

    def _render_portrait(self, tile_image: Image.Image, address: TileAddress) -> Image.Image:
        target_w = self._settings.portrait_work_width
        target_h = self._settings.portrait_height

        tile_dim = tile_image.width  # square tile guaranteed
        target_aspect = target_w / target_h
        crop_h = tile_dim
        crop_w = max(1, min(tile_dim, int(round(crop_h * target_aspect))))

        center_x = int(round(address.pixel_x))
        center_y = int(round(address.pixel_y))
        left = center_x - crop_w // 2
        top = center_y - crop_h // 2
        left = max(0, min(tile_dim - crop_w, left))
        top = max(0, min(tile_dim - crop_h, top))

        cropped = tile_image.crop((left, top, left + crop_w, top + crop_h))
        resized = cropped.resize((target_w, target_h), Image.LANCZOS)
        return self._trim_portrait(resized)

    def _trim_portrait(self, working_image: Image.Image) -> Image.Image:
        trim_left = self._settings.portrait_trim_left
        trim_right = self._settings.portrait_trim_right
        width, height = working_image.size
        return working_image.crop((trim_left, 0, width - trim_right, height))

    def _enforce_quota(self) -> None:
        now = time.time()
        hour_started = self._state.get("hour_started", 0)
        if now - hour_started >= 3600:
            self._state["hour_started"] = now
            self._state["requests_this_hour"] = 0
        elif self._state.get("requests_this_hour", 0) >= self._settings.mapbox_max_requests_per_hour:
            raise RuntimeError("Mapbox hourly request budget exhausted")

    def _record_portrait_usage(self, lat: float, lon: float, portrait_path: Path) -> None:
        self._state.update(
            {
                "last_lat": lat,
                "last_lon": lon,
                "current_portrait_path": str(portrait_path),
            }
        )
        self._persist_state()

    def _fallback_static_portrait(self, lat: float, lon: float) -> Image.Image:
        url = self._build_static_image_url(lat, lon)
        self._enforce_quota()
        response = self._session.get(url, timeout=30)
        response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as image:
            working = image.convert("RGB").copy()
        self._state["requests_this_hour"] = self._state.get("requests_this_hour", 0) + 1
        self._persist_state()
        return self._trim_portrait(working)

    def _build_static_image_url(self, lat: float, lon: float) -> str:
        pin_color = self._settings.pin_color.lstrip("#") or "ED1C24"
        zoom = self._settings.mapbox_zoom
        bearing = getattr(self._settings, "mapbox_bearing", 0.0)
        width = self._settings.portrait_work_width
        height = self._settings.portrait_height
        token = self._settings.mapbox_token
        return (
            "https://api.mapbox.com/styles/v1/"
            f"{self._settings.mapbox_username}/{self._settings.mapbox_style_id}/static/"
            f"pin-s+{pin_color}({lon:.6f},{lat:.6f})/"
            f"{lon:.6f},{lat:.6f},{zoom},{bearing:.1f}/"
            f"{width}x{height}?access_token={token}&logo=false&attribution=false"
        )


__all__ = ["MapboxClient", "haversine_km"]
