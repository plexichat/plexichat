"""
User Features Module - Manages user feature flags, badges, and rate limit tiers.

This module provides:
- Feature flags - admin-controlled
- Profile badges (alpha_tester, staff, etc.) - admin-controlled, displayed on profile
- Rate limit tiers (standard, alpha, premium) - determines API rate limits
- Configurable tier definitions with specific limits

Features are stored separately from auth_users to allow:
- Clean separation of concerns
- Easy querying of feature status
- Admin-only modification (users cannot change their own features)

Configuration (in config.yaml):
    user_features:
      alpha_registration_enabled: false  # Set true to auto-grant alpha tier to new users
      rate_limit_tiers:
        standard:
          multiplier: 1.0
          max_voice_minutes_per_day: 120
          max_video_minutes_per_day: 60
          max_file_uploads_per_day: 50
          max_file_size_mb: 10
          max_servers: 100
        alpha:
          multiplier: 2.0
          max_voice_minutes_per_day: 480
          max_video_minutes_per_day: 240
          max_file_uploads_per_day: 200
          max_file_size_mb: 25
          max_servers: 200
        premium:
          multiplier: 3.0
          max_voice_minutes_per_day: -1  # unlimited
          max_video_minutes_per_day: -1
          max_file_uploads_per_day: 500
          max_file_size_mb: 100
          max_servers: 500
      default_tier: standard
      badge_display_limit: 5
      available_badges:
        - alpha_tester
        - early_supporter
        - staff
        - verified
        - bug_hunter
        - contributor

Usage:
    from src.core import features
    features.setup(db)

    # Get user's tier
    tier = features.get_user_tier(user_id)

    # Get tier limits
    limits = features.get_tier_limits(tier)

    # Check feature flag
    can_voice = features.has_feature(user_id, "can_voice")

    # Get badges
    badges = features.get_user_badges(user_id)
"""

import json
import time
from typing import Optional, List, Any

import utils.logger as logger
import utils.config as config

from .schema import create_tables
from .models import (
    UserFeatures,
    TierLimits,
    Badge,
    AVAILABLE_BADGES,
    DEFAULT_TIER_LIMITS,
)
from .exceptions import (
    FeatureError,
    InvalidTierError,
    InvalidBadgeError,
)

_db: Any = None
_setup_complete = False


def setup(db: Any) -> None:
    """Initialize the features module."""
    global _db, _setup_complete

    _db = db
    create_tables(db)
    _setup_complete = True
    logger.info("User features module initialized")


def is_setup() -> bool:
    """Check if module is initialized."""
    return _setup_complete


def _get_db():
    """Get database instance."""
    if not _setup_complete:
        raise RuntimeError(
            "Features module not initialized. Call features.setup(db) first."
        )
    if _db is None:
        raise RuntimeError("Features database not set")
    return _db


def _get_config(key: str, default: Any = None) -> Any:
    """Get features configuration value."""
    features_config = config.get("user_features", {})
    keys = key.split(".")
    value = features_config
    for k in keys:
        if isinstance(value, dict):
            value = value.get(k, default)
        else:
            return default
    return value if value is not None else default


# === Tier Management ===


def get_available_tiers() -> List[str]:
    """Get list of available rate limit tiers."""
    tiers_config = _get_config("rate_limit_tiers", {})
    return (
        list(tiers_config.keys()) if tiers_config else ["standard", "alpha", "premium"]
    )


def get_tier_limits(tier: str) -> TierLimits:
    """
    Get limits for a specific tier.

    Args:
        tier: Tier name (e.g., 'standard', 'alpha', 'premium')

    Returns:
        TierLimits object with all limits for that tier
    """
    tiers_config = _get_config("rate_limit_tiers", {})

    if tier not in tiers_config:
        # Fall back to defaults
        if tier in DEFAULT_TIER_LIMITS:
            return DEFAULT_TIER_LIMITS[tier]
        raise InvalidTierError(f"Unknown tier: {tier}")

    tier_config = tiers_config[tier]
    return TierLimits(
        name=tier,
        multiplier=tier_config.get("multiplier", 1.0),
        max_voice_minutes_per_day=tier_config.get("max_voice_minutes_per_day", 120),
        max_video_minutes_per_day=tier_config.get("max_video_minutes_per_day", 60),
        max_file_uploads_per_day=tier_config.get("max_file_uploads_per_day", 50),
        max_file_size_mb=tier_config.get("max_file_size_mb", 10),
        max_servers=tier_config.get("max_servers", 100),
        max_message_length=tier_config.get("max_message_length", 2000),
        max_reactions_per_message=tier_config.get("max_reactions_per_message", 20),
        max_pins_per_channel=tier_config.get("max_pins_per_channel", 50),
        custom_emoji_slots=tier_config.get("custom_emoji_slots", 50),
    )


def get_default_tier() -> str:
    """Get the default tier for new users."""
    return _get_config("default_tier", "standard")


def is_alpha_registration_enabled() -> bool:
    """
    Check if alpha registration mode is enabled.

    When enabled, new user registrations automatically receive:
    - The 'alpha' tier (higher limits)
    - The 'alpha_tester' badge

    Returns:
        True if alpha registration mode is active
    """
    return _get_config("alpha_registration_enabled", False)


def apply_new_user_features(user_id: int) -> Optional[UserFeatures]:
    """
    Apply default features to a newly registered user.

    Called automatically after user registration. If alpha registration
    mode is enabled, grants alpha tier and alpha_tester badge.

    Args:
        user_id: The newly registered user's ID

    Returns:
        UserFeatures if any were applied, None otherwise
    """
    if not is_alpha_registration_enabled():
        return None

    db = _get_db()
    now = int(time.time() * 1000)

    from src.utils.encryption import generate_snowflake_id

    feature_id = generate_snowflake_id()

    # Grant alpha tier and alpha_tester badge
    badges = json.dumps(["alpha_tester"])

    db.execute(
        """INSERT INTO user_features 
           (id, user_id, rate_limit_tier, badges, 
            granted_by, granted_at, expires_at, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            feature_id,
            user_id,
            "alpha",
            badges,
            None,
            now,
            None,
            "Auto-granted: Alpha registration",
        ),
    )

    logger.info(f"Applied alpha tester features to new user {user_id}")

    updated = get_user_features(user_id)
    if updated is None:
        raise RuntimeError("Failed to load user features after update")
    return updated


# === User Features ===


def get_user_features(user_id: int) -> Optional[UserFeatures]:
    """
    Get all features for a user.

    Args:
        user_id: User ID

    Returns:
        UserFeatures object or None if no features set
    """
    db = _get_db()

    row = db.fetch_one(
        """SELECT id, user_id, rate_limit_tier, badges,
                  granted_by, granted_at, expires_at, notes
           FROM user_features WHERE user_id = ?""",
        (user_id,),
    )

    if not row:
        return None

    badges = []
    if row["badges"]:
        try:
            badges = json.loads(row["badges"])
        except (json.JSONDecodeError, TypeError):
            badges = []

    return UserFeatures(
        id=row["id"],
        user_id=row["user_id"],
        rate_limit_tier=row["rate_limit_tier"] or get_default_tier(),
        badges=badges,
        granted_by=row["granted_by"],
        granted_at=row["granted_at"],
        expires_at=row["expires_at"],
        notes=row["notes"],
    )


def get_user_tier(user_id: int) -> str:
    """
    Get user's rate limit tier.

    Args:
        user_id: User ID

    Returns:
        Tier name (defaults to 'standard' if not set)
    """
    features = get_user_features(user_id)
    if features:
        # Check if expired
        if features.expires_at and features.expires_at < int(time.time() * 1000):
            return get_default_tier()
        return features.rate_limit_tier
    return get_default_tier()


def get_user_tier_limits(user_id: int) -> TierLimits:
    """
    Get the tier limits for a specific user.

    Args:
        user_id: User ID

    Returns:
        TierLimits for the user's current tier
    """
    tier = get_user_tier(user_id)
    return get_tier_limits(tier)


def has_feature(user_id: int, feature: str) -> bool:
    """
    Check if user has a specific feature flag.

    Args:
        user_id: User ID
        feature: Feature name (e.g., 'can_voice')

    Returns:
        True if user has the feature
    """
    features = get_user_features(user_id)
    if not features:
        return False

    # Check expiration
    if features.expires_at and features.expires_at < int(time.time() * 1000):
        return False

    return getattr(features, feature, False)


def get_user_badges(user_id: int) -> List[str]:
    """
    Get user's profile badges.

    Args:
        user_id: User ID

    Returns:
        List of badge names
    """
    features = get_user_features(user_id)
    if not features:
        return []

    # Respect display limit
    limit = _get_config("badge_display_limit", 5)
    return features.badges[:limit]


# === Admin Functions ===


def set_user_features(
    user_id: int,
    admin_id: int,
    rate_limit_tier: Optional[str] = None,
    expires_at: Optional[int] = None,
    notes: Optional[str] = None,
) -> UserFeatures:
    """
    Set or update user features (admin only).

    Args:
        user_id: Target user ID
        admin_id: Admin performing the action
        rate_limit_tier: Rate limit tier name
        expires_at: Unix timestamp when features expire (None = permanent)
        notes: Admin notes

    Returns:
        Updated UserFeatures object
    """
    db = _get_db()

    # Validate tier if provided
    if rate_limit_tier:
        available = get_available_tiers()
        if rate_limit_tier not in available:
            raise InvalidTierError(
                f"Invalid tier '{rate_limit_tier}'. Available: {available}"
            )

    existing = get_user_features(user_id)
    now = int(time.time() * 1000)

    if existing:
        # Update existing
        updates = []
        params = []

        if rate_limit_tier is not None:
            updates.append("rate_limit_tier = ?")
            params.append(rate_limit_tier)

        if expires_at is not None:
            updates.append("expires_at = ?")
            params.append(expires_at)

        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)

        updates.append("granted_by = ?")
        params.append(admin_id)
        updates.append("granted_at = ?")
        params.append(now)

        params.append(user_id)

        db.execute(
            f"UPDATE user_features SET {', '.join(updates)} WHERE user_id = ?",
            tuple(params),
        )

        logger.info(f"Updated features for user {user_id} by admin {admin_id}")
    else:
        # Create new
        from src.utils.encryption import generate_snowflake_id

        feature_id = generate_snowflake_id()

        db.execute(
            """INSERT INTO user_features 
               (id, user_id, rate_limit_tier, badges, 
                granted_by, granted_at, expires_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                feature_id,
                user_id,
                rate_limit_tier or get_default_tier(),
                "[]",  # Empty badges
                admin_id,
                now,
                expires_at,
                notes,
            ),
        )

        logger.info(f"Created features for user {user_id} by admin {admin_id}")

    updated = get_user_features(user_id)
    if updated is None:
        raise RuntimeError("Failed to load user features after update")
    return updated


def add_badge(user_id: int, admin_id: int, badge: str) -> List[str]:
    """
    Add a badge to user's profile (admin only).

    Args:
        user_id: Target user ID
        admin_id: Admin performing the action
        badge: Badge name to add

    Returns:
        Updated list of badges
    """
    db = _get_db()

    # Validate badge
    available = _get_config("available_badges", AVAILABLE_BADGES)
    if badge not in available:
        raise InvalidBadgeError(f"Invalid badge '{badge}'. Available: {available}")

    features = get_user_features(user_id)

    if not features:
        # Create features entry first
        set_user_features(user_id, admin_id)
        features = get_user_features(user_id)

    if features is None:
        raise RuntimeError("Failed to load user features before adding badge")

    badges = features.badges.copy()
    if badge not in badges:
        badges.append(badge)

        db.execute(
            "UPDATE user_features SET badges = ?, granted_by = ?, granted_at = ? WHERE user_id = ?",
            (json.dumps(badges), admin_id, int(time.time() * 1000), user_id),
        )

        # Invalidate profile cache
        try:
            from src.core.database import cache_delete, redis_available
            if redis_available():
                cache_delete(f"user_profile:{user_id}")
        except Exception:
            pass

        logger.info(f"Added badge '{badge}' to user {user_id} by admin {admin_id}")

    return badges


def remove_badge(user_id: int, admin_id: int, badge: str) -> List[str]:
    """
    Remove a badge from user's profile (admin only).

    Args:
        user_id: Target user ID
        admin_id: Admin performing the action
        badge: Badge name to remove

    Returns:
        Updated list of badges
    """
    db = _get_db()

    features = get_user_features(user_id)
    if not features:
        return []

    badges = features.badges.copy()
    if badge in badges:
        badges.remove(badge)

        db.execute(
            "UPDATE user_features SET badges = ?, granted_by = ?, granted_at = ? WHERE user_id = ?",
            (json.dumps(badges), admin_id, int(time.time() * 1000), user_id),
        )

        # Invalidate profile cache
        try:
            from src.core.database import cache_delete, redis_available
            if redis_available():
                cache_delete(f"user_profile:{user_id}")
        except Exception:
            pass

        logger.info(f"Removed badge '{badge}' from user {user_id} by admin {admin_id}")

    return badges


def set_user_tier(
    user_id: int, admin_id: int, tier: str, expires_at: Optional[int] = None
) -> str:
    """
    Set user's rate limit tier (admin only).

    Args:
        user_id: Target user ID
        admin_id: Admin performing the action
        tier: Tier name
        expires_at: When tier expires (None = permanent)

    Returns:
        The tier that was set
    """
    set_user_features(user_id, admin_id, rate_limit_tier=tier, expires_at=expires_at)
    return tier


def get_rate_limit_multiplier(user_id: int) -> float:
    """
    Get the rate limit multiplier for a user.

    This is used by the rate limiting middleware to adjust limits.

    Args:
        user_id: User ID

    Returns:
        Multiplier (e.g., 1.0 for standard, 2.0 for alpha)
    """
    tier = get_user_tier(user_id)
    limits = get_tier_limits(tier)
    return limits.multiplier


__all__ = [
    "setup",
    "is_setup",
    "get_available_tiers",
    "get_tier_limits",
    "get_default_tier",
    "get_user_features",
    "get_user_tier",
    "get_user_tier_limits",
    "has_feature",
    "get_user_badges",
    "set_user_features",
    "add_badge",
    "remove_badge",
    "set_user_tier",
    "get_rate_limit_multiplier",
    "is_alpha_registration_enabled",
    "apply_new_user_features",
    "UserFeatures",
    "TierLimits",
    "Badge",
    "FeatureError",
    "InvalidTierError",
    "InvalidBadgeError",
]
