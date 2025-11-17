"""Tests for geographic footer descriptions."""

from __future__ import annotations

import pytest

from iss_display.pipeline.layout import describe_groundtrack


@pytest.mark.parametrize(
    "latitude, longitude, expected",
    [
        (0.0, 20.0, "Africa"),
        (52.0, 10.0, "Europe"),
        (40.0, -100.0, "North America"),
        (-15.0, -60.0, "South America"),
        (-25.0, 135.0, "Australia"),
        (0.0, -150.0, "the Pacific Ocean"),
        (-10.0, 90.0, "the Indian Ocean"),
        (80.0, 0.0, "the Arctic Circle"),
        (-80.0, 40.0, "Antarctica"),
        (0.0, 200.0, "the Pacific Ocean"),
    ],
)
def test_describe_groundtrack(latitude: float, longitude: float, expected: str) -> None:
    assert describe_groundtrack(latitude, longitude) == expected
