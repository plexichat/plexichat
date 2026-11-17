"""Regressions for residual feature/rollout hardening gaps.

Three weaknesses survived the earlier boundary sweep:

* ``float('nan')`` passed the rollout-percentage range check (both comparisons
  are false), so it could poison a tier multiplier or crash a gate; a huge int
  raised an uncaught ``OverflowError`` instead of a hardening error.
* Target lists were fully materialized with ``list(value)`` before the cap was
  checked, so an endless generator could never be rejected.
* ``has_feature`` returned the raw attribute for any key matching the declared
  grammar, so truthy non-flag attributes looked like granted flags.
"""

import pytest

from src.core import features
from src.core.features.models import UserFeatures
from src.core.features.validation import (
    MAX_ROLLOUT_TARGETS,
    feature_enabled_for,
    validate_rollout_percentage,
    validate_target_list,
    validate_tier_limits,
)
from src.core.security.hardening import HardeningViolation


class TestNonFinitePercentages:
    @pytest.mark.parametrize(
        "value",
        [float("nan"), "nan", float("inf"), "-inf", 10**400],
    )
    def test_rejects_non_finite_and_overflowing_values(self, value):
        with pytest.raises(HardeningViolation):
            validate_rollout_percentage(value)

    @pytest.mark.parametrize("value", [0, 12.5, 100, "50", True])
    def test_does_not_weaken_valid_values(self, value):
        assert validate_rollout_percentage(value) == float(value)

    def test_nan_cannot_poison_a_tier_multiplier(self):
        with pytest.raises(HardeningViolation):
            validate_tier_limits({"multiplier": float("nan")})

    def test_nan_cannot_reach_a_feature_gate(self):
        with pytest.raises(HardeningViolation):
            feature_enabled_for(1, rollout_percentage=float("nan"))


class TestBoundedTargetIteration:
    def test_oversized_generator_rejected(self):
        def targets():
            for i in range(MAX_ROLLOUT_TARGETS + 5):
                yield i + 1

        with pytest.raises(HardeningViolation):
            validate_target_list(targets())

    def test_endless_generator_is_capped(self):
        def endless():
            i = 1
            while True:
                yield i
                i += 1

        # Returns (rather than hangs) because iteration stops at the cap.
        with pytest.raises(HardeningViolation):
            validate_target_list(endless())

    def test_does_not_weaken_valid_lists(self):
        assert validate_target_list([1, 2, 123456789]) == [1, 2, 123456789]
        assert validate_target_list(None) == []


class TestHasFeatureRequiresBoolean:
    def test_non_boolean_attributes_are_not_flags(self, db, monkeypatch):
        features.setup(db)
        row = UserFeatures(
            id=1,
            user_id=1,
            rate_limit_tier="premium",
            badges=["alpha_tester"],
            notes="vip trial",
        )
        monkeypatch.setattr(features, "get_user_features", lambda user_id: row)
        for key in ("notes", "rate_limit_tier", "badges", "user_id", "to_dict"):
            assert features.has_feature(1, key) is False

    def test_genuine_boolean_flag_still_grants(self, db, monkeypatch):
        features.setup(db)
        row = UserFeatures(id=1, user_id=1)
        setattr(row, "can_voice", True)
        monkeypatch.setattr(features, "get_user_features", lambda user_id: row)
        assert features.has_feature(1, "can_voice") is True
        setattr(row, "can_voice", False)
        assert features.has_feature(1, "can_voice") is False
