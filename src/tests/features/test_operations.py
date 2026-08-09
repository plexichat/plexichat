"""
Tests for the user features module.

Covers:
- Module lifecycle (setup/is_setup, uninitialized guard)
- Tier management (available tiers, limits, defaults, invalid tier)
- Alpha registration mode
- User features CRUD (create, update, expiry, badges)
- Admin functions (set features, add/remove badges, set tier)
- Rate limit multiplier integration
- Data models (Badge, TierLimits, UserFeatures) and exceptions
"""

import json
import time

import pytest

from src.core import features
from src.core.features.models import (
    Badge,
    TierLimits,
    UserFeatures,
    AVAILABLE_BADGES,
    DEFAULT_TIER_LIMITS,
)
from src.core.features.exceptions import (
    FeatureError,
    InvalidTierError,
    InvalidBadgeError,
    UsageLimitExceededError,
)


# =============================================================================
# Module Lifecycle
# =============================================================================


class TestModuleLifecycle:
    """Test setup/is_setup and the uninitialized guard."""

    def test_not_setup_guard(self, monkeypatch):
        """Calls before setup must raise RuntimeError."""
        monkeypatch.setattr(features, "_setup_complete", False)
        monkeypatch.setattr(features, "_db", None)

        with pytest.raises(RuntimeError):
            features.get_user_features(1)

    def test_setup_marks_initialized(self, db, monkeypatch):
        """setup() must mark the module as initialized."""
        monkeypatch.setattr(features, "_setup_complete", False)
        monkeypatch.setattr(features, "_db", None)
        assert features.is_setup() is False
        features.setup(db)
        assert features.is_setup() is True

    def test_get_db_raises_when_db_none(self, monkeypatch):
        """_get_db must raise when the db handle is None."""
        monkeypatch.setattr(features, "_setup_complete", True)
        monkeypatch.setattr(features, "_db", None)

        with pytest.raises(RuntimeError):
            features._get_db()


# =============================================================================
# Tier Management
# =============================================================================


class TestTierManagement:
    """Test tier discovery and limit resolution."""

    def test_default_tiers_available(self, db):
        """Without user_features config the three standard tiers must exist."""
        features.setup(db)
        tiers = features.get_available_tiers()
        assert set(tiers) == {"standard", "alpha", "premium"}

    def test_config_driven_tiers(self, db):
        """Tiers defined in config must be returned verbatim."""
        features.setup(db)
        from utils.config import _config_instance as loader

        loader.config["user_features"] = {"rate_limit_tiers": {"platinum": {}}}
        loader._invalidate_cache()
        try:
            tiers = features.get_available_tiers()
            assert tiers == ["platinum"]
        finally:
            loader.config.pop("user_features", None)
            loader._invalidate_cache()

    def test_default_tier_fallback(self, db):
        """get_default_tier must default to 'standard'."""
        features.setup(db)
        assert features.get_default_tier() == "standard"

    def test_tier_limits_from_defaults(self, db):
        """Standard tier limits must match the documented defaults."""
        features.setup(db)
        limits = features.get_tier_limits("standard")
        assert limits.multiplier == 1.0
        assert limits.max_servers == 100
        assert limits.max_message_length == 2000

    def test_tier_limits_from_config_override(self, db):
        """Config tier values must win over defaults."""
        features.setup(db)
        from utils.config import _config_instance as loader

        loader.config["user_features"] = {
            "rate_limit_tiers": {"custom": {"multiplier": 4.0, "max_servers": 999}}
        }
        loader._invalidate_cache()
        try:
            limits = features.get_tier_limits("custom")
            assert limits.name == "custom"
            assert limits.multiplier == 4.0
            assert limits.max_servers == 999
            # Unset fields fall back to module defaults
            assert limits.max_voice_minutes_per_day == 120
        finally:
            loader.config.pop("user_features", None)
            loader._invalidate_cache()

    def test_invalid_tier_raises(self, db):
        """An unknown tier must raise InvalidTierError."""
        features.setup(db)
        with pytest.raises(InvalidTierError):
            features.get_tier_limits("nonexistent_tier")

    def test_premium_unlimited_voice(self, db):
        """Premium tier must be marked unlimited (-1)."""
        features.setup(db)
        limits = features.get_tier_limits("premium")
        assert limits.max_voice_minutes_per_day == -1
        assert limits.max_video_minutes_per_day == -1


# =============================================================================
# Alpha Registration
# =============================================================================


class TestAlphaRegistration:
    """Test alpha registration mode behavior."""

    def test_alpha_disabled_by_default(self, db):
        """Alpha registration must be disabled without config."""
        features.setup(db)
        assert features.is_alpha_registration_enabled() is False

    def test_alpha_enabled_via_config(self, db):
        """Alpha registration must be toggleable via config."""
        features.setup(db)
        from utils.config import _config_instance as loader

        loader.config["user_features"] = {"alpha_registration_enabled": True}
        loader._invalidate_cache()
        try:
            assert features.is_alpha_registration_enabled() is True
        finally:
            loader.config.pop("user_features", None)
            loader._invalidate_cache()

    def test_apply_new_user_features_disabled(self, db, test_user):
        """When disabled, no features are applied and None is returned."""
        features.setup(db)
        result = features.apply_new_user_features(test_user.id)
        assert result is None
        assert features.get_user_features(test_user.id) is None

    def test_apply_new_user_features_grants_alpha(self, db, test_user):
        """When enabled, a new user gets alpha tier and the alpha_tester badge."""
        features.setup(db)
        from utils.config import _config_instance as loader

        loader.config["user_features"] = {"alpha_registration_enabled": True}
        loader._invalidate_cache()
        try:
            result = features.apply_new_user_features(test_user.id)
            assert result is not None
            assert result.rate_limit_tier == "alpha"
            assert "alpha_tester" in result.badges
            # Persisted
            stored = features.get_user_features(test_user.id)
            assert stored.rate_limit_tier == "alpha"
            assert stored.badges == ["alpha_tester"]
        finally:
            loader.config.pop("user_features", None)
            loader._invalidate_cache()


# =============================================================================
# User Features Lookup
# =============================================================================


class TestUserFeaturesLookup:
    """Test feature lookup and tier resolution for users."""

    def test_get_user_features_none(self, db, test_user):
        """A user with no features row must return None."""
        features.setup(db)
        assert features.get_user_features(test_user.id) is None

    def test_get_user_tier_default(self, db, test_user):
        """A user with no features row must get the default tier."""
        features.setup(db)
        assert features.get_user_tier(test_user.id) == "standard"

    def test_get_user_tier_set(self, db, test_user):
        """A user with an explicit tier must see it."""
        features.setup(db)
        features.set_user_features(test_user.id, admin_id=1, rate_limit_tier="premium")
        assert features.get_user_tier(test_user.id) == "premium"

    def test_expired_tier_falls_back(self, db, test_user):
        """An expired tier must fall back to the default tier."""
        features.setup(db)
        features.set_user_features(
            test_user.id,
            admin_id=1,
            rate_limit_tier="premium",
            expires_at=int(time.time() * 1000) - 1000,
        )
        assert features.get_user_tier(test_user.id) == "standard"

    def test_get_user_tier_limits(self, db, test_user):
        """Tier limits must follow the user's tier."""
        features.setup(db)
        features.set_user_features(test_user.id, admin_id=1, rate_limit_tier="alpha")
        limits = features.get_user_tier_limits(test_user.id)
        assert limits.multiplier == 2.0
        assert limits.max_servers == 200

    def test_has_feature_no_features(self, db, test_user):
        """has_feature must return False for a user without features."""
        features.setup(db)
        assert features.has_feature(test_user.id, "any_feature") is False

    def test_has_feature_expired(self, db, test_user):
        """has_feature must return False once features expire."""
        features.setup(db)
        features.set_user_features(
            test_user.id,
            admin_id=1,
            expires_at=int(time.time() * 1000) - 1000,
        )
        assert features.has_feature(test_user.id, "any_feature") is False

    def test_get_user_badges_none(self, db, test_user):
        """A user without badges must get an empty list."""
        features.setup(db)
        assert features.get_user_badges(test_user.id) == []

    def test_badge_display_limit(self, db, test_user):
        """Only badge_display_limit badges must be returned."""
        features.setup(db)
        features.set_user_features(test_user.id, admin_id=1)
        for badge in AVAILABLE_BADGES[:6]:
            features.add_badge(test_user.id, admin_id=1, badge=badge)
        from utils.config import _config_instance as loader

        loader.config["user_features"] = {"badge_display_limit": 3}
        loader._invalidate_cache()
        try:
            badges = features.get_user_badges(test_user.id)
            assert len(badges) == 3
            assert badges == AVAILABLE_BADGES[:3]
        finally:
            loader.config.pop("user_features", None)
            loader._invalidate_cache()

    def test_corrupt_badges_json_survives(self, db, test_user):
        """A corrupt badges JSON column must not crash lookups."""
        features.setup(db)
        features.set_user_features(test_user.id, admin_id=1)
        db.execute(
            "UPDATE user_features SET badges = ? WHERE user_id = ?",
            ("{not-json", test_user.id),
        )
        stored = features.get_user_features(test_user.id)
        assert stored.badges == []


# =============================================================================
# Admin Functions
# =============================================================================


class TestAdminFunctions:
    """Test admin feature management."""

    def test_set_user_features_create(self, db, test_user):
        """Setting features for a new user must create a row."""
        features.setup(db)
        result = features.set_user_features(
            test_user.id, admin_id=1, rate_limit_tier="alpha", notes="test note"
        )
        assert result.rate_limit_tier == "alpha"
        assert result.notes == "test note"
        assert result.granted_by == 1
        assert result.granted_at > 0

    def test_set_user_features_update(self, db, test_user):
        """Updating an existing user must preserve the row."""
        features.setup(db)
        features.set_user_features(test_user.id, admin_id=1, rate_limit_tier="alpha")
        result = features.set_user_features(
            test_user.id, admin_id=2, rate_limit_tier="premium"
        )
        assert result.rate_limit_tier == "premium"
        assert result.granted_by == 2

    def test_set_user_features_invalid_tier(self, db, test_user):
        """An invalid tier must be rejected."""
        features.setup(db)
        with pytest.raises(InvalidTierError):
            features.set_user_features(
                test_user.id, admin_id=1, rate_limit_tier="bogus"
            )

    def test_add_badge(self, db, test_user):
        """Adding a valid badge must persist it."""
        features.setup(db)
        badges = features.add_badge(test_user.id, admin_id=1, badge="staff")
        assert badges == ["staff"]
        stored = features.get_user_features(test_user.id)
        assert stored.badges == ["staff"]

    def test_add_badge_creates_features_row(self, db, test_user):
        """Adding a badge to a user without features must create the row."""
        features.setup(db)
        features.add_badge(test_user.id, admin_id=1, badge="verified")
        stored = features.get_user_features(test_user.id)
        assert stored is not None
        assert stored.badges == ["verified"]

    def test_add_badge_dedup(self, db, test_user):
        """Adding the same badge twice must not duplicate it."""
        features.setup(db)
        features.add_badge(test_user.id, admin_id=1, badge="staff")
        badges = features.add_badge(test_user.id, admin_id=1, badge="staff")
        assert badges == ["staff"]
        assert features.get_user_badges(test_user.id) == ["staff"]

    def test_add_invalid_badge(self, db, test_user):
        """An unlisted badge must be rejected."""
        features.setup(db)
        with pytest.raises(InvalidBadgeError):
            features.add_badge(test_user.id, admin_id=1, badge="not_a_badge")

    def test_remove_badge(self, db, test_user):
        """Removing a present badge must persist the change."""
        features.setup(db)
        features.add_badge(test_user.id, admin_id=1, badge="staff")
        features.add_badge(test_user.id, admin_id=1, badge="verified")
        badges = features.remove_badge(test_user.id, admin_id=1, badge="staff")
        assert badges == ["verified"]
        assert features.get_user_badges(test_user.id) == ["verified"]

    def test_remove_missing_badge(self, db, test_user):
        """Removing an absent badge must be a no-op."""
        features.setup(db)
        features.add_badge(test_user.id, admin_id=1, badge="staff")
        badges = features.remove_badge(test_user.id, admin_id=1, badge="verified")
        assert badges == ["staff"]

    def test_remove_badge_no_features(self, db, test_user):
        """Removing a badge from a user without features must return []."""
        features.setup(db)
        assert features.remove_badge(test_user.id, admin_id=1, badge="staff") == []

    def test_set_user_tier(self, db, test_user):
        """set_user_tier must delegate and return the tier."""
        features.setup(db)
        result = features.set_user_tier(test_user.id, admin_id=1, tier="alpha")
        assert result == "alpha"
        assert features.get_user_tier(test_user.id) == "alpha"

    def test_get_rate_limit_multiplier(self, db, test_user):
        """The multiplier must follow the user's tier."""
        features.setup(db)
        assert features.get_rate_limit_multiplier(test_user.id) == 1.0
        features.set_user_tier(test_user.id, admin_id=1, tier="premium")
        assert features.get_rate_limit_multiplier(test_user.id) == 3.0


# =============================================================================
# Data Models
# =============================================================================


class TestDataModels:
    """Test the features data models."""

    def test_badge_info_known(self):
        """Known badges must have display metadata."""
        info = Badge.get_badge_info("staff")
        assert info.name == "staff"
        assert info.display_name == "Staff"
        assert info.icon == "⚡"

    def test_badge_info_unknown(self):
        """Unknown badges must produce a default badge, not crash."""
        info = Badge.get_badge_info("mystery")
        assert info.name == "mystery"
        assert info.icon == "🏷️"

    def test_tier_limits_to_dict(self):
        """to_dict must include all limit fields."""
        limits = TierLimits(name="standard", multiplier=1.0, max_servers=100)
        d = limits.to_dict()
        assert d["name"] == "standard"
        assert d["multiplier"] == 1.0
        assert d["max_servers"] == 100
        assert d["max_message_length"] == 2000

    def test_default_tier_limits_complete(self):
        """Every default tier must define the core limits."""
        for tier in ("standard", "alpha", "premium"):
            limits = DEFAULT_TIER_LIMITS[tier]
            assert limits.multiplier > 0
            assert limits.max_servers > 0
            assert limits.max_file_uploads_per_day > 0

    def test_user_features_get_tier_limits(self):
        """UserFeatures must resolve limits through its tier."""
        uf = UserFeatures(id=1, user_id=42, rate_limit_tier="alpha")
        limits = uf.get_tier_limits()
        assert limits.multiplier == 2.0

    def test_user_features_unknown_tier_falls_back(self):
        """An unknown tier on a UserFeatures must resolve to standard."""
        uf = UserFeatures(id=1, user_id=42, rate_limit_tier="bogus")
        limits = uf.get_tier_limits()
        assert limits.name == "standard"

    def test_user_features_get_badge_info(self):
        """get_badge_info must map every badge name."""
        uf = UserFeatures(id=1, user_id=42, badges=["staff", "verified"])
        infos = uf.get_badge_info()
        assert [i.name for i in infos] == ["staff", "verified"]

    def test_user_features_to_dict(self):
        """to_dict must expose public + expiry fields."""
        uf = UserFeatures(id=1, user_id=42, rate_limit_tier="premium", badges=["staff"])
        d = uf.to_dict()
        assert d["user_id"] == "42"
        assert d["rate_limit_tier"] == "premium"
        assert d["badges"] == ["staff"]

    def test_user_features_to_public_dict(self):
        """to_public_dict must not leak admin metadata."""
        uf = UserFeatures(
            id=1,
            user_id=42,
            rate_limit_tier="premium",
            badges=["staff"],
            granted_by=7,
            notes="secret",
        )
        d = uf.to_public_dict()
        assert d == {"badges": ["staff"], "tier": "premium"}


# =============================================================================
# Exceptions
# =============================================================================


class TestExceptions:
    """Test the features exception hierarchy."""

    def test_exception_hierarchy(self):
        """All feature exceptions must derive from FeatureError."""
        assert issubclass(InvalidTierError, FeatureError)
        assert issubclass(InvalidBadgeError, FeatureError)
        assert issubclass(UsageLimitExceededError, FeatureError)

    def test_usage_limit_exceeded_fields(self):
        """UsageLimitExceededError must carry limit details."""
        err = UsageLimitExceededError(
            "voice limit hit", limit_type="voice", current=121, max_allowed=120
        )
        assert err.limit_type == "voice"
        assert err.current == 121
        assert err.max_allowed == 120
        assert "voice limit hit" in str(err)
