"""Boundary tests for rate-limit config coercion and bucket bounds (commit 1).

A malformed config value or an attacker-varying bucket key must never produce
a negative count, an overflow, or an unbounded map. These tests pin the
coercion fallbacks and the LRU bound.
"""

import pytest

from src.core.ratelimit.config_validation import (
    MAX_SCOPE_BURST,
    MAX_SCOPE_REQUESTS,
    coerce_burst,
    coerce_config_float,
    coerce_config_int,
    coerce_multiplier,
    coerce_optional_int,
    coerce_requests,
    coerce_window,
)
from src.core.ratelimit.manager.bucket_bounds import (
    MAX_BUCKETS,
    BoundedBucketMap,
    bound_existing_buckets,
)


class TestConfigIntCoercion:
    def test_valid_value_passes_through(self):
        assert coerce_config_int(10, "x", minimum=0, maximum=100, default=5) == 10

    def test_numeric_string_is_accepted(self):
        assert coerce_config_int("10", "x", minimum=0, maximum=100, default=5) == 10

    @pytest.mark.parametrize("bad", [-1, 101, "abc", None, 1.5, True, 10**30])
    def test_invalid_values_fall_back(self, bad):
        assert coerce_config_int(bad, "x", minimum=0, maximum=100, default=5) == 5

    def test_none_falls_back(self):
        assert coerce_config_int(None, "x", minimum=0, maximum=100, default=7) == 7


class TestConfigFloatCoercion:
    def test_valid_float(self):
        assert coerce_config_float(2.5, "x", minimum=0, maximum=10, default=1.0) == 2.5

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 11, "x", True])
    def test_invalid_float_falls_back(self, bad):
        assert coerce_config_float(bad, "x", minimum=0, maximum=10, default=1.0) == 1.0


class TestSpecialisedCoercers:
    def test_requests_capped_and_non_negative(self):
        assert coerce_requests(500, "r", 60) == 500
        assert coerce_requests(-5, "r", 60) == 60
        assert coerce_requests(MAX_SCOPE_REQUESTS + 1, "r", 60) == 60

    def test_burst_capped_and_non_negative(self):
        assert coerce_burst(10, "b", 5) == 10
        assert coerce_burst(-1, "b", 5) == 5
        assert coerce_burst(MAX_SCOPE_BURST + 1, "b", 5) == 5

    def test_window_strictly_positive_and_bounded(self):
        assert coerce_window(30.0, "w", 60.0) == 30.0
        assert coerce_window(0, "w", 60.0) == 60.0
        assert coerce_window(-1, "w", 60.0) == 60.0
        assert coerce_window(10**9, "w", 60.0) == 60.0

    def test_multiplier_non_negative(self):
        assert coerce_multiplier(2.0, "m", 1.0) == 2.0
        assert coerce_multiplier(-1.0, "m", 1.0) == 1.0
        assert coerce_multiplier(10**9, "m", 1.0) == 1.0

    def test_optional_int(self):
        assert coerce_optional_int(None, "o", maximum=100) is None
        assert coerce_optional_int("", "o", maximum=100) is None
        assert coerce_optional_int(50, "o", maximum=100) == 50
        assert coerce_optional_int(-1, "o", maximum=100, default=9) == 9
        assert coerce_optional_int(1000, "o", maximum=100, default=9) == 9


class TestBoundedBucketMap:
    def test_evicts_oldest_at_capacity(self):
        m = BoundedBucketMap(capacity=2)
        m["a"] = 1
        m["b"] = 2
        m["c"] = 3
        assert len(m) == 2
        assert "a" not in m
        assert m.evictions == 1

    def test_access_refreshes_lru(self):
        m = BoundedBucketMap(capacity=2)
        m["a"] = 1
        m["b"] = 2
        assert m["a"] == 1  # refresh a
        m["c"] = 3
        assert "b" not in m
        assert "a" in m and "c" in m

    def test_capacity_validated(self):
        for bad in (0, -1, MAX_BUCKETS + 1, True, 1.5):
            with pytest.raises((TypeError, ValueError)):
                BoundedBucketMap(capacity=bad)

    def test_key_validated(self):
        m = BoundedBucketMap(capacity=2)
        with pytest.raises(ValueError):
            m[""] = 1

    def test_bound_existing_keeps_newest(self):
        source = {str(i): i for i in range(10)}
        result = bound_existing_buckets(source, capacity=3)
        assert len(result) == 3
        assert set(result) == {"7", "8", "9"}
