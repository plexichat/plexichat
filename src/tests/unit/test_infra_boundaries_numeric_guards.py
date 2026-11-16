"""Overflow guards for derived rate-limit numbers (theme 2 follow-up)."""

import pytest

from src.core.ratelimit.manager.numeric_guards import (
    guard_effective_limit,
    safe_add,
    safe_multiply,
)


class TestSafeAdd:
    def test_adds_within_cap(self):
        assert safe_add(10, 5, cap=20) == 15

    def test_rejects_cap_overflow(self):
        with pytest.raises(ValueError):
            safe_add(15, 10, cap=20)

    @pytest.mark.parametrize("bad", [-1, True, 1.5, 2**200])
    def test_rejects_invalid_operands(self, bad):
        with pytest.raises(ValueError):
            safe_add(bad, 1)


class TestSafeMultiply:
    def test_multiplies_within_cap(self):
        assert safe_multiply(10, 2.5, cap=100) == 25

    @pytest.mark.parametrize("bad", [-1, True, float("inf"), float("nan"), "2", 2**200])
    def test_rejects_invalid_multiplier(self, bad):
        with pytest.raises(ValueError):
            safe_multiply(10, bad)

    def test_rejects_product_overflow(self):
        with pytest.raises(ValueError):
            safe_multiply(100, 100, cap=1000)

    def test_int_multiplier(self):
        assert safe_multiply(7, 3) == 21


class TestEffectiveLimit:
    def test_normal(self):
        assert guard_effective_limit(10, 5) == 15

    def test_overflow_rejected(self):
        with pytest.raises(ValueError):
            guard_effective_limit(10**30, 5)
