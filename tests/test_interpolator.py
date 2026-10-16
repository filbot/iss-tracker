"""Tests for ISSOrbitInterpolator timing and dead-reckoning behavior."""

from types import SimpleNamespace

import pytest

from iss_display.app import main
from iss_display.app.main import ISSOrbitInterpolator
from iss_display.data.iss_client import ISSFetchError, ISSFix


class FakeClient:
    """Returns queued fixes (or raises queued errors) from get_fix()."""

    def __init__(self):
        self.queue = []

    def get_fix(self):
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def reset_session(self):
        pass


@pytest.fixture
def clock(monkeypatch):
    """Controllable monotonic and wall clocks patched into main.time."""
    c = SimpleNamespace(mono=1000.0, wall=1_700_000_000.0)
    monkeypatch.setattr(main, "time", SimpleNamespace(
        monotonic=lambda: c.mono,
        time=lambda: c.wall,
        sleep=lambda _s: None,
    ))
    return c


def fix(lat, lon):
    return ISSFix(latitude=lat, longitude=lon, altitude_km=420.0,
                  velocity_kmh=27600.0, timestamp=0.0)


def test_data_age_ignores_wall_clock_steps(clock):
    client = FakeClient()
    interp = ISSOrbitInterpolator(client)
    client.queue.append(fix(10.0, 20.0))
    interp._do_fetch()

    # NTP steps the wall clock forward 40 minutes; only 10s really passed.
    clock.wall += 2400
    clock.mono += 10

    assert interp.get_telemetry().data_age_sec == pytest.approx(10.0)


def test_no_fix_returns_none_until_first_fetch(clock):
    client = FakeClient()
    interp = ISSOrbitInterpolator(client)
    client.queue.append(ISSFetchError("network down"))
    interp._do_fetch()

    assert interp.get_telemetry() is None

    client.queue.append(fix(10.0, 20.0))
    interp._do_fetch()
    assert interp.get_telemetry().latitude == pytest.approx(10.0)
