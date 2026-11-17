"""Boundary tests for rate-limit caps, negatives and overflows (commit 2).

Every caller-influenced number in the limiter now has an upper bound as well
as a lower one. These tests pin the caps so a finite-but-absurd value cannot
sneak past the old "non-negative and finite" checks.
"""

import math
from typing import Any

import pytest

from src.core.ratelimit.manager.validation import (
    MAX_AMOUNT,
    MAX_BURST,
    MAX_COST,
    MAX_LIMIT,
    MAX_MULTIPLIER,
    MAX_REQUESTS,
    MAX_RETRY_AFTER_PRECISION,
    MAX_TTL,
    MAX_WINDOW_SECONDS,
    normalise_amount,
    normalise_cost,
    validate_config,
    validate_custom_counter_args,
    validate_multiplier,
)
from src.core.ratelimit.models import RateLimitConfig


def _config(**overrides):
    defaults: dict[str, Any] = dict(
        requests=10,
        window_seconds=60.0,
        burst=5,
        hourly_limit=100,
        daily_limit=1000,
        retry_after_precision=1,
    )
    defaults.update(overrides)
    return RateLimitConfig(**defaults)


class TestValidateConfigCaps:
    def test_valid_config_accepted(self):
        assert validate_config(_config()) is not None

    @pytest.mark.parametrize(
        "field,cap", [("requests", MAX_REQUESTS), ("burst", MAX_BURST)]
    )
    def test_negative_and_over_cap_rejected(self, field, cap):
        with pytest.raises(ValueError):
            validate_config(_config(**{field: -1}))
        with pytest.raises(ValueError):
            validate_config(_config(**{field: cap + 1}))
        assert validate_config(_config(**{field: cap})) is not None

    def test_requests_and_burst_reject_overflow_ints(self):
        huge = 2**200
        with pytest.raises(ValueError):
            validate_config(_config(requests=huge))
        with pytest.raises(ValueError):
            validate_config(_config(burst=10**400))

    @pytest.mark.parametrize(
        "bad", [0, -1, 1e-9, MAX_WINDOW_SECONDS + 1, math.inf, math.nan]
    )
    def test_window_bounds(self, bad):
        with pytest.raises(ValueError):
            validate_config(_config(window_seconds=bad))

    def test_window_max_accepted(self):
        assert validate_config(_config(window_seconds=MAX_WINDOW_SECONDS)) is not None

    @pytest.mark.parametrize("field", ["hourly_limit", "daily_limit"])
    def test_hourly_daily_bounds(self, field):
        with pytest.raises(ValueError):
            validate_config(_config(**{field: -1}))
        with pytest.raises(ValueError):
            validate_config(_config(**{field: MAX_LIMIT + 1}))
        assert validate_config(_config(**{field: None})) is not None

    @pytest.mark.parametrize("bad", [-1, MAX_RETRY_AFTER_PRECISION + 1, 1.5, True])
    def test_retry_after_precision(self, bad):
        with pytest.raises(ValueError):
            validate_config(_config(retry_after_precision=bad))


class TestCostAndAmountCaps:
    @pytest.mark.parametrize("bad", [-1, MAX_COST + 1, True, 1.5])
    def test_normalise_cost_rejects(self, bad):
        with pytest.raises((ValueError, TypeError)):
            normalise_cost(bad)

    def test_normalise_cost_accepts_bounded(self):
        assert normalise_cost(0) == 0
        assert normalise_cost(MAX_COST) == MAX_COST

    def test_normalise_amount_rejects_over_cap(self):
        with pytest.raises(ValueError):
            normalise_amount(MAX_AMOUNT + 1)
        assert normalise_amount(MAX_AMOUNT) == MAX_AMOUNT


class TestMultiplierAndTtlCaps:
    @pytest.mark.parametrize("bad", [-1, MAX_MULTIPLIER + 1, math.inf, math.nan])
    def test_multiplier_rejects(self, bad):
        with pytest.raises(ValueError):
            validate_multiplier(bad, "m")

    def test_multiplier_accepts_bounded(self):
        assert validate_multiplier(MAX_MULTIPLIER, "m") == MAX_MULTIPLIER

    def test_counter_ttl_rejects_over_cap(self):
        with pytest.raises(ValueError):
            validate_custom_counter_args("k", "f", MAX_TTL + 1)
        validate_custom_counter_args("k", "f", MAX_TTL)
