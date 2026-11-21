"""Regression: non-finite floats must clamp to the safe end, not the loudest.

``max(0.0, min(1.0, float(v)))`` is a common idiom that looks bounds-safe but
is not: every comparison against NaN is False, so ``min(1.0, nan)`` returns
``1.0`` and garbage input becomes *maximum* volume. The shared
``clamp_finite_float`` helper must degrade unusable values to the caller's
safe default instead.
"""

import math


from src.core.security.hardening import clamp_finite_float


def test_nan_and_infinities_degrade_to_default():
    for bad in (float("nan"), float("inf"), float("-inf"), "nan", "inf", "-inf"):
        assert clamp_finite_float(bad, 0.0, 1.0, default=0.0) == 0.0


def test_invalid_types_and_bool_degrade_to_default():
    for bad in (None, object(), [], {}, True, False):
        assert clamp_finite_float(bad, 0.0, 1.0, default=0.0) == 0.0


def test_overflowing_integer_degrades_to_default():
    # float(10**400) raises OverflowError; the naive clamp propagated it as a
    # 500. It must degrade to the safe default instead.
    assert clamp_finite_float(10**400, 0.0, 1.0, default=0.0) == 0.0


def test_finite_values_are_clamped_normally():
    assert clamp_finite_float(0.5, 0.0, 1.0, default=0.0) == 0.5
    assert clamp_finite_float(2.0, 0.0, 1.0, default=0.0) == 1.0
    assert clamp_finite_float(-2.0, 0.0, 1.0, default=0.0) == 0.0
    assert clamp_finite_float("0.25", 0.0, 1.0, default=0.0) == 0.25


def test_naive_clamp_would_have_failed_open():
    """Pin the exact bug this helper exists to prevent."""
    naive = max(0.0, min(1.0, float("nan")))
    assert math.isnan(naive) is False
    assert naive == 1.0  # documented fail-open behaviour of the old idiom
    assert clamp_finite_float(float("nan"), 0.0, 1.0, default=0.0) == 0.0
