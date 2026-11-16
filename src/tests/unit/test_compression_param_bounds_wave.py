"""Compression quality settings must be bounded before reaching Pillow."""

import pytest

from src.core.media.compression import _bounded_image_quality
from src.core.security.hardening import HardeningViolation


@pytest.mark.parametrize("value", [0, -5, 101, 10**9, "abc", None])
def test_rejects_out_of_range_quality(value):
    with pytest.raises(HardeningViolation):
        _bounded_image_quality(value)


@pytest.mark.parametrize("value", [1, 60, 85, 100, "75"])
def test_accepts_valid_quality(value):
    assert 1 <= _bounded_image_quality(value) <= 100
