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

import itertools
import json
import threading
import time
import weakref
from collections import OrderedDict
from copy import deepcopy
from typing import Optional, List, Any

import utils.logger as logger
import utils.config as config

from src.core.security.hardening import SNOWFLAKE_MAX, bounded_int
from src.core.config.snapshot import VersionedConfigSnapshot, file_version

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
from .validation import (
    MAX_ROLLOUT_TARGETS,
    feature_enabled_for,
    validate_feature_flags,
    validate_feature_key,
    validate_feature_value,
    validate_tier_limits,
)

_db: Any = None
_setup_complete = False

# Tier/flag lookups all funnel through ``get_user_features``. Rate limiting and
# several route helpers call it repeatedly for the same user, so keep a short
# TTL cache in front of the DB read. Grants with an expiry are deliberately not
# cached so the time-based checks in ``get_user_tier``/``has_feature`` stay
# authoritative. Every mutation path invalidates the affected user.
#
# The store is an LRU rather than a plain dict with "clear everything when
# full" eviction. With more active users than the cap, a full flush on every
# overflow dropped every hot entry at once: the next request for each of those
# users re-queried the DB, turning the cap into a periodic stampede. Evicting a
# single least-recently-used key keeps hot users cached and bounds the store.
_FEATURES_CACHE_TTL = 5.0
_FEATURES_CACHE_MAX = 1024
_features_cache: "OrderedDict[tuple[Any, int], tuple[float, Optional[UserFeatures]]]" = (
    OrderedDict()
)
_features_cache_lock = threading.Lock()
_FEATURES_CACHE_STATS = {"hits": 0, "misses": 0, "evictions": 0}

# A monotonic, weak token identifies the backing database. ``id(db)`` alone is
# recyclable: once a db object is collected a new one can reuse its address and
# observe a stale feature row cached for a different database. The same
# WeakKeyDictionary pattern is used by the admin caches. A non-weakrefable
# double falls back to its identity plus its configured path.
_DB_TOKENS: "weakref.WeakKeyDictionary[Any, tuple[int, Any]]" = (
    weakref.WeakKeyDictionary()
)
_DB_TOKENS_LOCK = threading.Lock()
_DB_TOKEN_COUNTER = itertools.count(1)


def _features_db_token(db: Any) -> tuple:
    """Stable, non-recycled identity for ``db`` without caching any rows."""
    cfg = getattr(db, "config", None)
    path = cfg.get("path") if isinstance(cfg, dict) else None
    try:
        with _DB_TOKENS_LOCK:
            token = _DB_TOKENS.get(db)
            if token is None:
                token = (next(_DB_TOKEN_COUNTER), path)
                _DB_TOKENS[db] = token
            return token
    except TypeError:
        # Unhashable / non-weakrefable double: keep distinct objects distinct
        # using their identity (plus the path) as the fallback key.
        return (-1, id(db), path)


def get_features_cache_stats() -> dict:
    """Snapshot of the user-features cache counters and current size."""
    with _features_cache_lock:
        return {
            **_FEATURES_CACHE_STATS,
            "size": len(_features_cache),
            "max_entries": _FEATURES_CACHE_MAX,
        }


def _invalidate_user_features_cache(user_id: int) -> None:
    """Drop cached feature rows for ``user_id`` across all configured DBs."""
    with _features_cache_lock:
        for key in [k for k in _features_cache if k[1] == user_id]:
            _features_cache.pop(key, None)


def setup(db: Any) -> None:
    """Initialize the features module."""
    global _db, _setup_complete

    _db = db
    _setup_complete = True
    with _features_cache_lock:
        _features_cache.clear()
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


def _copy_tier_limits(limits: TierLimits) -> TierLimits:
    """Return an independent copy of a memoized tier-limit verdict.

    ``TierLimits`` is a mutable dataclass and the resolved table is cached
    process-wide in ``_tier_limits_snapshot`` (keyed only by config version).
    Handing the cached instance to a caller would let one in-place edit (e.g.
    ``limits.max_file_size_mb = -1`` or ``limits.multiplier = 100``) silently
    widen the upload/rate-limit/emoji-slot gates every later request sees for
    the whole config version. Copy on both the store and read paths so the
    memoized object is never reachable by a caller.
    """
    return deepcopy(limits)


def _compute_tier_limits(tier: str) -> TierLimits:
    """Resolve one tier's limits from config (uncached, authoritative).

    This is the single source of truth for a tier's limits. It is used both by
    the cached ``get_tier_limits`` hot path and by the snapshot builder, so
    caching never changes *how* a verdict is computed -- only how often.
    """
    tiers_config = _get_config("rate_limit_tiers", {})

    if tier not in tiers_config:
        # Fail closed: an unknown tier is rejected, a known one falls back to
        # its built-in limits. No cached verdict can bypass this branch. The
        # default is copied so the cached table never aliases the module-level
        # DEFAULT_TIER_LIMITS singleton that other callers can reach.
        if tier in DEFAULT_TIER_LIMITS:
            return _copy_tier_limits(DEFAULT_TIER_LIMITS[tier])
        raise InvalidTierError(f"Unknown tier: {tier}")

    # Tier limits come from trusted operator YAML; -1 is the documented
    # "unlimited" sentinel used by the staff tier for several fields.
    tier_config = validate_tier_limits(tiers_config[tier], allow_unlimited=True)
    return TierLimits(
        name=tier,
        multiplier=tier_config.get("multiplier", 1.0),
        max_voice_minutes_per_day=tier_config.get("max_voice_minutes_per_day", 120),
        max_video_minutes_per_day=tier_config.get("max_video_minutes_per_day", 60),
        max_file_uploads_per_day=tier_config.get("max_file_uploads_per_day", 50),
        max_file_size_mb=tier_config.get("max_file_size_mb", 10),
        max_storage_mb=tier_config.get("max_storage_mb", 5 * 1024),
        max_servers=tier_config.get("max_servers", 100),
        max_message_length=tier_config.get("max_message_length", 2000),
        max_reactions_per_message=tier_config.get("max_reactions_per_message", 20),
        max_pins_per_channel=tier_config.get("max_pins_per_channel", 50),
        custom_emoji_slots=tier_config.get("custom_emoji_slots", 50),
    )


def get_tier_limits(tier: str) -> TierLimits:
    """
    Get limits for a specific tier.

    Args:
        tier: Tier name (e.g., 'standard', 'alpha', 'premium')

    Returns:
        TierLimits object with all limits for that tier

    Note:
        Rate limiting and the tier-limit helper call this for every request.
        The resolved table is memoized in a version-keyed, short-TTL snapshot
        (see ``_tier_limits_snapshot``), so the config read + validation +
        reconstruction happens once per config version instead of once per
        request. Unknown tiers and invalid config still raise and are never
        cached, so the gate remains fail-closed.

        A detached copy is returned on every read: ``TierLimits`` is mutable
        and feeds upload-size/rate-limit/emoji-slot decisions, so callers must
        never be able to mutate the shared memoized verdict.
    """
    table = _tier_limits_snapshot.snapshot()
    limits = table.get(tier)
    if limits is not None:
        return _copy_tier_limits(limits)
    # A tier absent from the configured table is either a known built-in
    # (default limits) or unknown (rejected). Both must be resolved by the
    # authoritative helper, never inferred from the cache.
    return _copy_tier_limits(_compute_tier_limits(tier))


def get_default_tier() -> str:
    """Get the default tier for new users."""
    return _get_config("default_tier", "standard")


# === Tier configuration snapshot ===
#
# ``get_available_tiers``/``get_tier_limits``/``get_default_tier`` are all
# derived from the same (largely static) ``user_features`` config section. The
# admin tier-listing endpoint used to call those helpers once per tier plus once
# for the default, re-reading the config and re-validating/reconstructing every
# TierLimits on each request. Build the whole table once and reuse it until the
# config actually changes.
#
# The version token follows the established config-snapshot pattern (see
# ``src.api.config``): it folds in the live loader identity, the config file's
# mtime/size, and a representation of only the two sections that affect this
# table. A new ``config.setup()``, an on-disk edit, or an in-memory change to
# ``rate_limit_tiers``/``default_tier`` therefore invalidates on the very next
# access with no staleness window, while an untouched config is served from
# memory forever. No validation is skipped -- the cached value is produced by
# the same helpers used everywhere else.


class TierConfiguration:
    """The computed tier table used by the admin tier-listing endpoint."""

    __slots__ = ("tiers", "limits", "default")

    def __init__(
        self, tiers: List[str], limits: dict[str, "TierLimits"], default: str
    ) -> None:
        self.tiers = tiers
        self.limits = limits
        self.default = default


def _tier_config_version() -> tuple:
    """Cheap token describing everything the tier table depends on."""
    loader = getattr(config, "_config_instance", None)
    path = getattr(loader, "config_path", None)
    try:
        features_config = config.get("user_features", {}) or {}
    except Exception:
        features_config = {}
    if not isinstance(features_config, dict):
        features_config = {}
    return (
        id(loader),
        file_version(path),
        repr(features_config.get("rate_limit_tiers")),
        repr(features_config.get("default_tier")),
    )


def _build_tier_configuration() -> TierConfiguration:
    """Build the tier table via the authoritative helpers (cache miss only).

    The admin table and the per-request limits snapshot have independent
    version checks. When this table rebuilds we drop the nested snapshot first
    so a config change is reflected here immediately rather than lingering for
    the per-request cache's short TTL. Normal rate-limit traffic never calls
    this builder, so its hot path stays cached.
    """
    _tier_limits_snapshot.invalidate()
    tiers = get_available_tiers()
    return TierConfiguration(
        tiers=tiers,
        limits={tier: get_tier_limits(tier) for tier in tiers},
        default=get_default_tier(),
    )


_tier_configuration_snapshot = VersionedConfigSnapshot(
    _build_tier_configuration, version_fn=_tier_config_version
)


def get_tier_configuration() -> TierConfiguration:
    """Return the current tier table, rebuilding only when config changes.

    ``TierConfiguration`` exposes a mutable tier list and a dict of mutable
    ``TierLimits``; both are detached here so a caller editing the returned
    table cannot poison the cached snapshot used by the admin tier listing
    (and, through the shared limit objects, downstream quota decisions).
    """
    cached = _tier_configuration_snapshot.snapshot()
    return TierConfiguration(
        tiers=list(cached.tiers),
        limits={
            tier: _copy_tier_limits(limits) for tier, limits in cached.limits.items()
        },
        default=cached.default,
    )


# === Per-request tier-limits resolution cache ===
#
# ``get_rate_limit_multiplier`` and ``get_user_tier_limits`` resolve a tier on
# every rate-limited request. Previously that re-read ``user_features`` config
# and re-ran ``validate_tier_limits`` + a full ``TierLimits`` reconstruction on
# each call. The resolved table is now a
# version-keyed snapshot with a short staleness ceiling:
#
# * *version-keyed* -- ``_tier_config_version`` folds in the config loader
#   identity, the config file's mtime/size, and the two sections this table
#   depends on. A new ``config.setup()``, an on-disk edit, or an in-memory
#   ``rate_limit_tiers``/``default_tier`` change yields a new version, so a
#   stale table can never be served, and the ceiling bounds how long a change
#   can go unnoticed when no explicit invalidate hook is called.
# * *short TTL* -- ``max_age`` throttles even the version check (which stats
#   the config file) on the hot path to at most once per ceiling window.
# * *bounded* -- the cached value is a table of tiers derived from trusted
#   operator config (not from request data), so its size is bounded by the
#   configured tier set; there is no per-request key that can grow without
#   limit.
#
# Fail-closed: the table is only ever produced by ``_compute_tier_limits``, so
# validation is never skipped. Unknown tiers/invalid config raise and are
# never cached; a tier absent from the table falls through to the same
# authoritative helper. Explicit invalidation is wired into
# ``invalidate_tier_configuration_cache`` for admin config-reload/hot-swap
# paths so no verdict survives a change beyond the TTL.
_TIER_LIMITS_CACHE_TTL_SECONDS = 5.0


def _build_tier_limits_table() -> dict[str, TierLimits]:
    """Build the resolved tier-limit table (cache miss only)."""
    return {tier: _compute_tier_limits(tier) for tier in get_available_tiers()}


_tier_limits_snapshot = VersionedConfigSnapshot(
    _build_tier_limits_table,
    version_fn=_tier_config_version,
    max_age=_TIER_LIMITS_CACHE_TTL_SECONDS,
)


def invalidate_tier_limits_cache() -> None:
    """Drop the per-request tier-limits snapshot immediately."""
    _tier_limits_snapshot.invalidate()


def invalidate_tier_configuration_cache() -> None:
    """Drop cached tier data so the next access rebuilds it.

    This is the single invalidation hook for admin config-reload / license
    hot-swap paths: it clears both the admin tier table and the per-request
    tier-limits snapshot, so no grant or limit can survive a change.
    """
    _tier_configuration_snapshot.invalidate()
    _tier_limits_snapshot.invalidate()


def warm_tier_limits_cache() -> int:
    """Prime both tier snapshots off the request path.

    The first rate-limited request (or admin tier listing) after a restart
    would otherwise pay the config read + ``validate_tier_limits`` + full
    ``TierLimits`` reconstruction on the request thread. Building the resolved
    table here moves that one-off cost to the background warm-up worker.

    Idempotent and best-effort: both snapshots are single-entry, version-keyed
    caches, so a second call within the same config version is a no-op. A bad
    or unavailable config must never fail startup -- the request path still
    builds on demand exactly as before -- so each step is isolated and errors
    are only logged. Returns the elapsed milliseconds, like ``warm_once``.
    """
    started = time.perf_counter()
    # Build the admin table first: its builder also resolves the per-request
    # limits table, so the explicit lookup below is then a pure cache hit.
    try:
        get_tier_configuration()
    except Exception as e:
        logger.debug(f"Startup warm-up tier configuration skipped: {e}")
    try:
        get_tier_limits(get_default_tier())
    except Exception as e:
        logger.debug(f"Startup warm-up tier limits skipped: {e}")
    return int((time.perf_counter() - started) * 1000.0)


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
    _invalidate_user_features_cache(user_id)

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

    # Key on a non-recycled DB token as well as the user so re-setup with a
    # fresh database can never serve a row cached from the previous one.
    cache_key = (_features_db_token(db), user_id)
    now = time.monotonic()
    with _features_cache_lock:
        cached = _features_cache.get(cache_key)
        if cached is not None and cached[0] > now:
            _features_cache.move_to_end(cache_key)
            _FEATURES_CACHE_STATS["hits"] += 1
            return cached[1]
        if cached is not None:
            # Expired entries must never be served; drop them on access.
            _features_cache.pop(cache_key, None)
        _FEATURES_CACHE_STATS["misses"] += 1

    row = db.fetch_one(
        """SELECT id, user_id, rate_limit_tier, badges,
                  granted_by, granted_at, expires_at, notes
           FROM user_features WHERE user_id = ?""",
        (user_id,),
    )

    if not row:
        result: Optional[UserFeatures] = None
    else:
        badges = []
        if row["badges"]:
            try:
                badges = json.loads(row["badges"])
            except (json.JSONDecodeError, TypeError):
                badges = []

        result = UserFeatures(
            id=row["id"],
            user_id=row["user_id"],
            rate_limit_tier=row["rate_limit_tier"] or get_default_tier(),
            badges=badges,
            granted_by=row["granted_by"],
            granted_at=row["granted_at"],
            expires_at=row["expires_at"],
            notes=row["notes"],
        )

    # Time-bound grants are never cached: expiry is evaluated by callers on
    # each read, so a cached object could otherwise keep a grant alive past
    # its expiry for the length of the TTL.
    if result is None or result.expires_at is None:
        # Start the TTL after the read so a slow DB call cannot shorten the
        # window in which the row is reused.
        expires = time.monotonic() + _FEATURES_CACHE_TTL
        with _features_cache_lock:
            _features_cache[cache_key] = (expires, result)
            _features_cache.move_to_end(cache_key)
            while len(_features_cache) > _FEATURES_CACHE_MAX:
                _features_cache.popitem(last=False)
                _FEATURES_CACHE_STATS["evictions"] += 1
    return result


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
    # SECURITY: the key is caller-supplied; reject dunder/attribute-style names
    # before it can be used with getattr on the UserFeatures instance.
    feature = validate_feature_key(feature)

    features = get_user_features(user_id)
    if not features:
        return False

    # Check expiration
    if features.expires_at and features.expires_at < int(time.time() * 1000):
        return False

    # Only an actual boolean ``True`` grants a flag. Returning the raw
    # attribute meant that public names which pass the declared key grammar
    # (``notes``, ``badges``, ``user_id``, ``to_dict``, ...) were treated as
    # granted whenever their value happened to be truthy. A caller passing a
    # user-influenced feature name could therefore bypass the flag check.
    granted = getattr(features, feature, False)
    return granted is True


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

    # Respect a bounded display limit; a negative or huge config value used to
    # slice everything (or nothing) without an explicit decision.
    limit = bounded_int(
        _get_config("badge_display_limit", 5),
        "badge_display_limit",
        minimum=0,
        maximum=MAX_ROLLOUT_TARGETS,
    )
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

    notes = validate_feature_value(notes, field="notes")
    if expires_at is not None:
        expires_at = bounded_int(
            expires_at, "expires_at", minimum=0, maximum=SNOWFLAKE_MAX
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
        _invalidate_user_features_cache(user_id)

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
        _invalidate_user_features_cache(user_id)

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

    badge = validate_feature_key(badge, field="badge")
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
        _invalidate_user_features_cache(user_id)

        # Invalidate profile cache
        try:
            from src.core.database import cache_delete, valkey_available

            if valkey_available():
                cache_delete(f"user_profile:{user_id}")
        except Exception as cache_error:
            logger.debug(f"feature cache invalidate failed: {cache_error}")

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
        _invalidate_user_features_cache(user_id)

        # Invalidate profile cache
        try:
            from src.core.database import cache_delete, valkey_available

            if valkey_available():
                cache_delete(f"user_profile:{user_id}")
        except Exception as cache_error:
            logger.debug(f"feature cache invalidate failed: {cache_error}")

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
    "get_tier_configuration",
    "invalidate_tier_configuration_cache",
    "invalidate_tier_limits_cache",
    "warm_tier_limits_cache",
    "TierConfiguration",
    "get_user_features",
    "get_features_cache_stats",
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
    "validate_feature_key",
    "validate_feature_value",
    "validate_feature_flags",
    "feature_enabled_for",
]
