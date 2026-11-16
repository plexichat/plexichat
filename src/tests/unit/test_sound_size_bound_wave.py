"""The soundboard upload cap must be a bounded, positive byte count."""

import pytest

from src.api.routes.soundboard import (
    _HTTP_UPLOAD_CEILING_BYTES,
    _bounded_sound_size,
)
from src.core.security.hardening import HardeningViolation


@pytest.mark.parametrize("value", [1, 2097152, _HTTP_UPLOAD_CEILING_BYTES, "1024"])
def test_valid_cap(value):
    assert 1 <= _bounded_sound_size(value) <= _HTTP_UPLOAD_CEILING_BYTES


@pytest.mark.parametrize(
    "value", [0, -1, _HTTP_UPLOAD_CEILING_BYTES + 1, 10**12, "abc"]
)
def test_invalid_cap(value):
    with pytest.raises(HardeningViolation):
        _bounded_sound_size(value)
