"""Declared-schema validation for feature flag keys and values."""

import pytest

from src.core import features
from src.core.features.validation import (
    FEATURE_KEY_PATTERN,
    MAX_FEATURE_VALUE_LENGTH,
    MAX_FLAGS_PER_REQUEST,
    RESERVED_FLAG_KEYS,
    validate_feature_flags,
    validate_feature_key,
    validate_feature_value,
)
from src.core.security.hardening import HardeningViolation


class TestFeatureKeys:
    def test_accepts_declared_grammar(self):
        for key in ("can_voice", "alpha_tester", "f1", "a" * 64):
            assert validate_feature_key(key) == key
            assert FEATURE_KEY_PATTERN.fullmatch(key)

    @pytest.mark.parametrize(
        "key",
        ["", "1bad", "BadKey", "bad-key", "bad key", "bad\nkey", "a" * 65, None],
    )
    def test_rejects_bad_keys(self, key):
        with pytest.raises(HardeningViolation):
            validate_feature_key(key)

    def test_reserved_keys_never_reachable(self):
        for key in RESERVED_FLAG_KEYS:
            with pytest.raises(HardeningViolation):
                validate_feature_key(key)


class TestFeatureValues:
    def test_scalar_types(self):
        assert validate_feature_value(True) is True
        assert validate_feature_value(None) is None
        assert validate_feature_value(7) == 7
        assert validate_feature_value("text") == "text"

    @pytest.mark.parametrize(
        "value",
        [
            -1,
            2**31,
            "a" * (MAX_FEATURE_VALUE_LENGTH + 1),
            "bad\nvalue",
            [],
            {"nested": 1},
            1.5,
        ],
    )
    def test_rejects_bad_values(self, value):
        with pytest.raises(HardeningViolation):
            validate_feature_value(value)


class TestFeatureFlagMapping:
    def test_valid_mapping(self):
        assert validate_feature_flags({"can_voice": True, "tier": "alpha"}) == {
            "can_voice": True,
            "tier": "alpha",
        }

    def test_bounded_and_validated(self):
        with pytest.raises(HardeningViolation):
            validate_feature_flags(
                {f"flag{i}": 1 for i in range(MAX_FLAGS_PER_REQUEST + 1)}
            )
        with pytest.raises(HardeningViolation):
            validate_feature_flags({"__class__": True})
        with pytest.raises(HardeningViolation):
            validate_feature_flags([])


class TestFeaturesIntegration:
    def test_has_feature_rejects_dunder_key(self, db):
        features.setup(db)
        with pytest.raises(HardeningViolation):
            features.has_feature(1, "__class__")
        with pytest.raises(HardeningViolation):
            features.has_feature(1, "toString")

    def test_set_user_features_validates_notes_and_expiry(self, db):
        features.setup(db)
        with pytest.raises(HardeningViolation):
            features.set_user_features(1, 2, notes="x" * (MAX_FEATURE_VALUE_LENGTH + 1))
        with pytest.raises(HardeningViolation):
            features.set_user_features(1, 2, expires_at=-1)

    def test_add_badge_validates_key(self, db):
        features.setup(db)
        with pytest.raises(HardeningViolation):
            features.add_badge(1, 2, "__class__")
