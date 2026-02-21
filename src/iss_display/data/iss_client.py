"""Minimal ISS telemetry client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

from iss_display.config import Settings

logger = logging.getLogger(__name__)

# Static fallback URLs tried after the primary fails.
# NOTE: open-notify.org was removed — it is HTTP-only, returns no altitude or
# velocity, and has been unreliably reachable since early 2026.
# N2YO is added dynamically in get_fix() when a key is configured.
FALLBACK_APIS: list[str] = []

# N2YO ISS position endpoint.  Observer coords 0/0/0 are ignored when only
# the satellite geocoords (satlatitude/satlongitude/sataltitude) are consumed.
_N2YO_URL = "https://api.n2yo.com/rest/v1/satellite/positions/25544/0/0/0/1/?apiKey={key}"

# Per-request timeouts: (connect_timeout, read_timeout) in seconds.
# A short connect timeout fails fast on unreachable hosts; a longer read
# timeout tolerates the occasional slow response from wheretheiss.at.
_TIMEOUT = (3.05, 8)


class ISSFetchError(Exception):
    """Raised when all APIs in the chain have failed."""


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

    def _build_api_list(self) -> list[str]:
        """Return the ordered list of URLs to try, including optional N2YO."""
        urls = [self._settings.iss_api_url] + FALLBACK_APIS
        if self._settings.n2yo_api_key:
            urls.append(_N2YO_URL.format(key=self._settings.n2yo_api_key))
        return urls

    def get_fix(self) -> ISSFix:
        """Try each API in sequence and return the first successful result.

        Raises ISSFetchError if all APIs fail, allowing the caller to handle
        backoff and caching decisions.
        """
        errors: list[str] = []

        for api_url in self._build_api_list():
            try:
                response = self._session.get(api_url, timeout=_TIMEOUT)
                response.raise_for_status()
                fix = self._parse_response(response.json())
                self._last_fix = fix
                return fix
            except Exception as e:
                logger.debug(f"API {api_url} failed: {e}")
                errors.append(f"{api_url}: {e}")

        raise ISSFetchError("; ".join(errors))

    def _parse_response(self, data: dict) -> ISSFix:
        # N2YO: {"positions": [{"satlatitude": ..., "satlongitude": ..., "sataltitude": ..., "timestamp": ...}]}
        if "positions" in data:
            pos = data["positions"][0]
            return ISSFix(
                latitude=float(pos["satlatitude"]),
                longitude=float(pos["satlongitude"]),
                altitude_km=_coerce_optional(pos.get("sataltitude")),
                velocity_kmh=None,  # N2YO does not provide velocity
                timestamp=float(pos.get("timestamp", 0.0)),
            )

        # wheretheiss.at: {"latitude": ..., "longitude": ..., "altitude": ..., "velocity": ..., "timestamp": ...}
        if "iss_position" in data:
            # open-notify.org legacy format (kept for any custom ISS_API_URL that uses it)
            return ISSFix(
                latitude=float(data["iss_position"]["latitude"]),
                longitude=float(data["iss_position"]["longitude"]),
                altitude_km=None,
                velocity_kmh=None,
                timestamp=float(data.get("timestamp", 0.0)),
            )

        return ISSFix(
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            altitude_km=_coerce_optional(data.get("altitude")),
            velocity_kmh=_coerce_optional(data.get("velocity")),
            timestamp=float(data.get("timestamp", 0.0)),
        )


def _coerce_optional(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["ISSClient", "ISSFetchError", "ISSFix"]
