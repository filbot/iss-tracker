"""Client for the People in Space API (corquaid/international-space-station-APIs)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

_ASTROS_URL = "https://corquaid.github.io/international-space-station-APIs/JSON/people-in-space.json"
_TIMEOUT = (3.05, 8)
_REFRESH_INTERVAL = 300.0  # 5 minutes
_RETRY_AFTER_FAILURE = 60.0  # back off to this cadence after a failed fetch


@dataclass(frozen=True)
class CrewMember:
    name: str
    craft: str


@dataclass(frozen=True)
class AstrosData:
    count: int
    crew: List[CrewMember]
    timestamp: float


class AstrosClient:
    """Fetches current astronauts in space from corquaid/international-space-station-APIs.

    Runs a background thread that refreshes every ~5 minutes.  Callers use
    ``get_astros()`` for an instant, non-blocking read of the cached value —
    no network I/O happens on the calling thread.
    """

    def __init__(self):
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._cached: Optional[AstrosData] = None
        self._last_fetch: float = 0.0
        self._consecutive_failures: int = 0

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Do one synchronous fetch and start the background refresh thread."""
        self._do_fetch()
        self._thread = threading.Thread(
            target=self._fetch_loop, name="astros-fetch", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def get_astros(self) -> Optional[AstrosData]:
        """Return the most recent cached crew data without blocking on I/O."""
        with self._lock:
            return self._cached

    def reset_session(self) -> None:
        """Close and recreate the HTTP session."""
        try:
            self._session.close()
        except Exception:
            logger.debug("Astros session close failed", exc_info=True)
        self._session = requests.Session()

    def _do_fetch(self) -> None:
        """Single fetch attempt; updates cache on success, leaves it on failure."""
        try:
            resp = self._session.get(_ASTROS_URL, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            if not isinstance(data, dict):
                raise ValueError(f"expected JSON object, got {type(data).__name__}")
            people = data.get("people", [])
            if not isinstance(people, list):
                raise ValueError("'people' is not a list")

            crew = []
            for p in people:
                if not isinstance(p, dict) or "name" not in p or "spacecraft" not in p:
                    raise ValueError(f"malformed crew entry: {p!r}")
                crew.append(CrewMember(name=p["name"], craft=p["spacecraft"]))

            new_data = AstrosData(
                count=data.get("number", len(crew)),
                crew=crew,
                timestamp=time.time(),
            )
            with self._lock:
                self._cached = new_data
                self._last_fetch = time.monotonic()
                self._consecutive_failures = 0
            logger.debug("Fetched astros: %d people", new_data.count)
        except Exception as e:
            with self._lock:
                self._consecutive_failures += 1
                failures = self._consecutive_failures
            logger.warning("Astros API failed (%dx): %s", failures, e)

    def _fetch_loop(self) -> None:
        """Background loop that refreshes the cache periodically."""
        while not self._stop.is_set():
            with self._lock:
                failures = self._consecutive_failures
            interval = _RETRY_AFTER_FAILURE if failures > 0 else _REFRESH_INTERVAL
            # wait() returns True if stopped, False on timeout — exit on stop
            if self._stop.wait(interval):
                return
            try:
                self._do_fetch()
            except Exception:
                # _do_fetch should already swallow errors, but guard the loop.
                logger.exception("Unhandled error in astros fetch loop")


__all__ = ["AstrosClient", "AstrosData", "CrewMember"]
