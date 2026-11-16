"""Retention day values from config, DB overrides and stored policies are bounded."""

import pytest

from src.core.artifacts.retention import _bounded_retention_days, _parse_policy_days


@pytest.mark.parametrize("value", [1, 30, 365, 36500, "90"])
def test_valid_days(value):
    assert _bounded_retention_days(value) == int(value)


@pytest.mark.parametrize("value", [0, -1, 36501, 10**12, "abc", None, ""])
def test_invalid_days(value):
    assert _bounded_retention_days(value) is None


@pytest.mark.parametrize("policy", [{"days": 10**12}, "99999999", -5])
def test_policy_days_are_bounded(policy):
    assert _parse_policy_days(policy) is None


def test_valid_policy_days():
    assert _parse_policy_days({"days": 45}) == 45
