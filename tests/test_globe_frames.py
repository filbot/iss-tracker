import numpy as np
import pytest

from iss_display.config import Settings
from iss_display.display.globe_frames import (
    FRAMES_PATH, GlobeFramesError, disc_size, load_frames,
)
from iss_display.theme import THEME


def test_committed_frames_match_theme():
    # Fails after a [globe] edit in theme.toml until the frames are
    # re-rendered with tools/render_globe.py and committed.
    settings = Settings.load()
    frames = load_frames(settings.display_width, settings.display_height)
    d = disc_size(settings.display_width, settings.display_height)
    assert frames.shape == (THEME.globe.num_frames, d, d)
    assert frames.dtype == np.dtype('>u2')


def test_stale_key_is_rejected(tmp_path):
    path = tmp_path / FRAMES_PATH.name
    np.savez_compressed(path, frames=np.zeros((1, 2, 2), '>u2'), key=np.array("stale"))
    with pytest.raises(GlobeFramesError, match="different"):
        load_frames(320, 480, path)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(GlobeFramesError, match="not found"):
        load_frames(320, 480, tmp_path / "nope.npz")
