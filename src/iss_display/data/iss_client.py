"""Minimal ISS telemetry client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from iss_display.config import Settings


@dataclass
class ISSFix:
    latitude: float
    longitude: float
    altitude_km: Optional[float]
    velocity_kmh: Optional[float]
    timestamp: float


class ISSClient:
    """Fetches the latest ISS position from a single API call."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = requests.Session()

    def get_fix(self) -> ISSFix:
        response = self._session.get(self._settings.iss_api_url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "iss_position" in data:
            lat = float(data["iss_position"]["latitude"])
            lon = float(data["iss_position"]["longitude"])
            altitude = None
            velocity = None
            timestamp = float(data.get("timestamp", 0.0))
        else:
            lat = float(data["latitude"])
            lon = float(data["longitude"])
            altitude = _coerce_optional(data.get("altitude"))
            velocity = _coerce_optional(data.get("velocity"))
            timestamp = float(data.get("timestamp", 0.0))

        return ISSFix(latitude=lat, longitude=lon, altitude_km=altitude, velocity_kmh=velocity, timestamp=timestamp)


def _coerce_optional(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["ISSClient", "ISSFix"]
