"""Minimal ISS telemetry client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

from iss_display.config import Settings

logger = logging.getLogger(__name__)

# Fallback APIs in case primary fails
FALLBACK_APIS = [
    "http://api.open-notify.org/iss-now.json",
    "https://api.wheretheiss.at/v1/satellites/25544",
]


@dataclass
class ISSFix:
    latitude: float
    longitude: float
    altitude_km: Optional[float]
    velocity_kmh: Optional[float]
    timestamp: float
    data_age_sec: float = 0.0              # Seconds since last successful API fetch


class ISSClient:
    """Fetches the latest ISS position from a single API call."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = requests.Session()
        self._last_fix: Optional[ISSFix] = None

    def get_fix(self) -> ISSFix:
        # Try primary API first, then fallbacks
        apis_to_try = [self._settings.iss_api_url] + FALLBACK_APIS
        
        for api_url in apis_to_try:
            try:
                response = self._session.get(api_url, timeout=5)
                response.raise_for_status()
                data = response.json()
                fix = self._parse_response(data)
                self._last_fix = fix
                return fix
            except Exception as e:
                logger.debug(f"API {api_url} failed: {e}")
                continue
        
        # If all APIs fail, return last known position or default
        if self._last_fix:
            logger.warning("All APIs failed, using last known position")
            return self._last_fix
        
        # Default position (over Pacific Ocean)
        logger.warning("All APIs failed, using default position")
        return ISSFix(latitude=0.0, longitude=-150.0, altitude_km=420.0, velocity_kmh=27600.0, timestamp=0.0)
    
    def _parse_response(self, data: dict) -> ISSFix:

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
