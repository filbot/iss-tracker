"""ISS telemetry client with caching and rate limiting."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from pathlib import Path
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
    """Lightweight client for the wheretheiss.at API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = requests.Session()
        self._state_file = settings.state_dir / "iss.json"
        self._last_fix: Optional[ISSFix] = None
        self._load_state()

    def _load_state(self) -> None:
        if self._state_file.exists():
            payload = json.loads(self._state_file.read_text())
            self._last_fix = ISSFix(**payload)

    def _persist_state(self) -> None:
        if self._last_fix is None:
            return
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._last_fix.__dict__, indent=2))

    def should_poll(self) -> bool:
        if self._last_fix is None:
            return True
        return (time.time() - self._last_fix.timestamp) >= self._settings.iss_poll_interval

    def get_fix(self, *, force: bool = False) -> ISSFix:
        if not force and not self.should_poll() and self._last_fix is not None:
            return self._last_fix

        response = self._session.get(self._settings.iss_api_url, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Normalize both possible schemas (wheretheiss.at or open-notify)
        if "iss_position" in data:
            lat = float(data["iss_position"]["latitude"])
            lon = float(data["iss_position"]["longitude"])
            altitude = None
            velocity = None
            timestamp = float(data.get("timestamp", time.time()))
        else:
            lat = float(data["latitude"])
            lon = float(data["longitude"])
            altitude = float(data.get("altitude")) if data.get("altitude") else None
            velocity = float(data.get("velocity")) if data.get("velocity") else None
            timestamp = float(data.get("timestamp", time.time()))

        self._last_fix = ISSFix(
            latitude=lat,
            longitude=lon,
            altitude_km=altitude,
            velocity_kmh=velocity,
            timestamp=timestamp,
        )
        self._persist_state()
        return self._last_fix

    @property
    def last_fix(self) -> Optional[ISSFix]:
        return self._last_fix


__all__ = ["ISSClient", "ISSFix"]
