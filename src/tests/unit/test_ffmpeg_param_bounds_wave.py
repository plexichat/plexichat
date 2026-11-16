"""ffmpeg arguments sourced from config must be constrained to safe tokens."""

import pytest

from src.core.media.compression import (
    _bounded_audio_bitrate,
    _bounded_ffmpeg_preset,
)
from src.core.security.hardening import HardeningViolation


@pytest.mark.parametrize("value", ["fast", "medium", "veryslow", "MEDIUM", " fast "])
def test_valid_presets(value):
    assert _bounded_ffmpeg_preset(value) in {"fast", "medium", "veryslow"}


@pytest.mark.parametrize("value", ["", "-y", "fast;rm -rf /", "abc", None, 10**9])
def test_invalid_presets(value):
    with pytest.raises(HardeningViolation):
        _bounded_ffmpeg_preset(value)


@pytest.mark.parametrize("value", ["128k", "96K", "2m", "320k"])
def test_valid_bitrates(value):
    assert re_ok(value)


def re_ok(value):
    return _bounded_audio_bitrate(value).islower()


@pytest.mark.parametrize("value", ["", "128kbps", "abc", "-y", None, "99999k"])
def test_invalid_bitrates(value):
    with pytest.raises(HardeningViolation):
        _bounded_audio_bitrate(value)
