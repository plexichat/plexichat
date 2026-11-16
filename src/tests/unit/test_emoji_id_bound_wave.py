"""Emoji route ids are snowflakes, not arbitrary integers."""

import pytest

from src.api.routes.emojis import _bounded_id
from src.core.security.hardening import HardeningViolation


@pytest.mark.parametrize("value", [1, 42, 2**63 - 1, "123"])
def test_valid_ids(value):
    assert _bounded_id(value, "server ID") == int(value)


@pytest.mark.parametrize("value", [0, -1, 2**63, 10**30, "abc", None, ""])
def test_invalid_ids(value):
    with pytest.raises(HardeningViolation):
        _bounded_id(value, "server ID")
