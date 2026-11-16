"""Caps on rollout percentages, forced-target lists and tier config numbers."""

import pytest

from src.core.features.validation import (
    MAX_ROLLOUT_TARGETS,
    ROLLOUT_BUCKETS,
    feature_enabled_for,
    validate_rollout_percentage,
    validate_target_list,
    validate_tier_limits,
)
from src.core.security.hardening import HardeningViolation
from src.core import features


class TestRolloutPercentage:
    @pytest.mark.parametrize("value", [0, 0.0, 12.5, 100, 100.0])
    def test_accepts_range(self, value):
        assert validate_rollout_percentage(value) == float(value)

    @pytest.mark.parametrize("value", [-1, 100.01, 1000, "abc", None])
    def test_rejects_out_of_range(self, value):
        with pytest.raises(HardeningViolation):
            validate_rollout_percentage(value)


class TestTargetLists:
    def test_accepts_snowflakes(self):
        assert validate_target_list([1, 2, 123456789]) == [1, 2, 123456789]
        assert validate_target_list(None) == []

    def test_caps_and_validates(self):
        with pytest.raises(HardeningViolation):
            validate_target_list([1] * (MAX_ROLLOUT_TARGETS + 1))
        with pytest.raises(HardeningViolation):
            validate_target_list([0, -1, 2**63])
        with pytest.raises(HardeningViolation):
            validate_target_list("not-a-list")


class TestFeatureEnabledFor:
    def test_extremes_and_determinism(self):
        user = 123456
        assert feature_enabled_for(user, rollout_percentage=0) is False
        assert feature_enabled_for(user, rollout_percentage=100) is True
        for pct in (1, 25, 50, 99):
            expected = user % ROLLOUT_BUCKETS < int(pct * ROLLOUT_BUCKETS / 100)
            assert feature_enabled_for(user, rollout_percentage=pct) is expected

    def test_targets_and_grant_override(self):
        user = 123456
        assert feature_enabled_for(user, rollout_percentage=0, targets=[user]) is True
        assert (
            feature_enabled_for(999, rollout_percentage=0, targets=[user], granted=True)
            is True
        )

    def test_bad_inputs_rejected(self):
        with pytest.raises(HardeningViolation):
            feature_enabled_for(1, rollout_percentage=101)
        with pytest.raises(HardeningViolation):
            feature_enabled_for(1, rollout_percentage=50, targets=[0])
        with pytest.raises(HardeningViolation):
            feature_enabled_for(0, rollout_percentage=50)


class TestTierConfigBounds:
    def test_bounds_each_number(self):
        result = validate_tier_limits({"max_servers": 10, "multiplier": 2.5})
        assert result["max_servers"] == 10
        assert result["multiplier"] == 2.5

    @pytest.mark.parametrize(
        "config",
        [
            {"max_servers": -5},
            {"max_servers": 10**9},
            {"multiplier": -1},
            {"multiplier": 1000},
        ],
    )
    def test_rejects_out_of_range(self, config):
        with pytest.raises(HardeningViolation):
            validate_tier_limits(config)

    def test_unknown_keys_ignored(self):
        assert validate_tier_limits({"unknown": 1}) == {}

    def test_module_exports_helper(self):
        assert features.feature_enabled_for is feature_enabled_for
