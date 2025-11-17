"""Tests for geographic footer descriptions."""

from __future__ import annotations

import pytest

from iss_display.pipeline.layout import describe_groundtrack


@pytest.mark.parametrize(
    "latitude, longitude, expected",
    [
        (0.0, 20.0, "Somewhere over Africa"),
        (52.0, 10.0, "Somewhere over Europe"),
        (40.0, -100.0, "Somewhere over North America"),
        (-15.0, -60.0, "Somewhere over South America"),
        (-25.0, 135.0, "Somewhere over Australia"),
        (0.0, -150.0, "Somewhere over the Pacific Ocean"),
        (-10.0, 90.0, "Somewhere over the Indian Ocean"),
        (80.0, 0.0, "Somewhere over the Arctic Circle"),
        (-80.0, 40.0, "Somewhere over Antarctica"),
        (0.0, 200.0, "Somewhere over the Pacific Ocean"),
    ],
)
def test_describe_groundtrack(latitude: float, longitude: float, expected: str) -> None:
    assert describe_groundtrack(latitude, longitude) == expected
