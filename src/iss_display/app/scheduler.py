"""Coordinates data collection, rendering, and display updates."""

from __future__ import annotations

import logging
import time
from typing import Tuple

from PIL import Image

from iss_display.config import Settings
from iss_display.data.iss_client import ISSClient, ISSFix
from iss_display.data.mapbox_client import MapboxClient
from iss_display.display.epaper_driver import DisplayDriver
from iss_display.pipeline.image_preprocessor import FrameEncoder
from iss_display.pipeline.layout import FrameLayout

LOGGER = logging.getLogger(__name__)


class DisplayScheduler:
    def __init__(
        self,
        settings: Settings,
        iss_client: ISSClient,
        mapbox_client: MapboxClient,
        layout: FrameLayout,
        encoder: FrameEncoder,
        driver: DisplayDriver,
    ) -> None:
        self._settings = settings
        self._iss_client = iss_client
        self._mapbox_client = mapbox_client
        self._layout = layout
        self._encoder = encoder
        self._driver = driver

    def build_frame(self, *, force: bool = False) -> Tuple[bytes, bytes, Image.Image]:
        fix = self._iss_client.get_fix(force=force or self._settings.force_refresh)
        base_map = self._mapbox_client.get_portrait_image(
            fix.latitude,
            fix.longitude,
            force=force or self._settings.force_refresh,
        )
        canvas = self._layout.compose(base_map, fix)
        red, black = self._encoder.encode(canvas)
        return red, black, canvas

    def refresh_once(self, *, force: bool = False) -> None:
        LOGGER.info("Refreshing display frame")
        red, black, canvas = self.build_frame(force=force)
        self._driver.display_frame(red, black, image=canvas)

    def run_forever(self) -> None:
        LOGGER.info("Starting scheduler loop with %s second cadence", self._settings.iss_poll_interval)
        while True:
            try:
                self.refresh_once()
            except Exception as exc:  # pragma: no cover - defensive logging
                LOGGER.exception("Display refresh failed: %s", exc)
            time.sleep(self._settings.iss_poll_interval)

    def close(self) -> None:
        self._driver.close()
