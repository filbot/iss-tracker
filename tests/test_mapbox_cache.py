import io
from pathlib import Path

import requests
from PIL import Image

from iss_display.config import Settings
from iss_display.data.mapbox_client import MapboxClient


class DummyResponse:
    def __init__(self, payload: bytes) -> None:
        self.content = payload

    def raise_for_status(self) -> None:  # pragma: no cover - trivially satisfied
        return None


class DummySession:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls = 0

    def get(self, url, timeout):  # noqa: D401 - signature matches requests
        self.calls += 1
        return DummyResponse(self.payload)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        mapbox_token="token",
        mapbox_username="user",
        mapbox_style_id="style",
        mapbox_zoom=1,
        mapbox_tile_size=512,
        mapbox_hidpi=False,
        mapbox_bearing=0.0,
        mapbox_refresh_radius_km=2000.0,
        mapbox_max_requests_per_hour=10,
        mapbox_enable_static_fallback=True,
        portrait_work_width=128,
        portrait_height=250,
        portrait_trim_left=3,
        portrait_trim_right=3,
        pin_color="#ED1C24",
        iss_api_url="https://example.com",
        iss_poll_interval=60,
        display_width=128,
        display_height=250,
        display_has_red=True,
        display_logical_width=122,
        display_pad_left=3,
        display_pad_right=3,
        led_enabled=False,
        led_pin=12,
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        preview_dir=tmp_path / "preview",
        preview_only=True,
        log_level="INFO",
        force_refresh=False,
    )


def make_tile_payload(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (512, 512), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def make_static_payload(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (128, 250), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


class TileFailureSession:
    def __init__(self, fallback_payload: bytes) -> None:
        self.payload = fallback_payload
        self.calls: list[str] = []

    def get(self, url, timeout):  # noqa: D401 - signature matches requests
        self.calls.append(url)
        if "/tiles/" in url:
            raise requests.RequestException("boom")
        return DummyResponse(self.payload)


def test_mapbox_client_reuses_cached_portrait(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    session = DummySession(make_tile_payload())
    client = MapboxClient(settings, session=session)  # type: ignore[arg-type]

    img1 = client.get_portrait_image(0.0, 0.0, force=True)
    assert session.calls == 1
    assert img1.size == (settings.display_logical_width, settings.portrait_height)

    img2 = client.get_portrait_image(0.0, 0.0)
    assert session.calls == 1  # served from portrait cache/state
    assert img2.size == img1.size

    img3 = client.get_portrait_image(0.01, 0.01)
    assert session.calls == 1  # still within refresh radius
    assert img3.size == img1.size


def test_mapbox_client_falls_back_to_static_image(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    session = TileFailureSession(make_static_payload())
    client = MapboxClient(settings, session=session)  # type: ignore[arg-type]

    img = client.get_portrait_image(10.0, 20.0, force=True)
    assert img.size == (settings.display_logical_width, settings.portrait_height)
    assert any("/static/" in url for url in session.calls)
