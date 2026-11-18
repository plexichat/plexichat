"""
User Features data models.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from src.core.base import SnowflakeID
from src.core.config.snapshot import VersionedConfigSnapshot, config_source_version


# Available badge types
AVAILABLE_BADGES = [
    "alpha_tester",  # Alpha testing participant
    "early_supporter",  # Early adopter
    "staff",  # Plexichat staff member
    "verified",  # Verified account
    "bug_hunter",  # Found and reported bugs
    "contributor",  # Code/docs contributor
    "moderator",  # Community moderator
    "partner",  # Partner program member
]


@dataclass
class Badge:
    """Represents a profile badge."""

    name: str
    display_name: str
    description: str
    icon: str  # Emoji or icon name
    color: str  # Hex color for display

    @staticmethod
    def get_badge_info(badge_name: str) -> "Badge":
        """Get display info for a badge.

        The badge catalogue is static operator/feature data, but this helper is
        on the profile/message hot path and used to rebuild the entire catalogue
        (eight ``Badge`` objects) on every call just to pick one entry.  It now
        reads a versioned snapshot instead.  A fresh ``Badge`` is returned so
        callers can never mutate the cached templates.
        """
        template = _badge_catalog_snapshot.snapshot().get(badge_name)
        if template is not None:
            return Badge(**vars(template))
        return Badge(
            name=badge_name,
            display_name=badge_name.replace("_", " ").title(),
            description="",
            icon="🏷️",
            color="#6b7280",
        )


def _build_badge_catalog() -> Dict[str, Badge]:
    """Build the static badge catalogue (only runs on a snapshot miss)."""
    return {
        "alpha_tester": Badge(
            name="alpha_tester",
            display_name="Alpha Tester",
            description="Participated in Plexichat alpha testing",
            icon="🧪",
            color="#9333ea",
        ),
        "early_supporter": Badge(
            name="early_supporter",
            display_name="Early Supporter",
            description="Supported Plexichat early on",
            icon="💎",
            color="#06b6d4",
        ),
        "staff": Badge(
            name="staff",
            display_name="Staff",
            description="Plexichat team member",
            icon="⚡",
            color="#ef4444",
        ),
        "verified": Badge(
            name="verified",
            display_name="Verified",
            description="Verified account",
            icon="✓",
            color="#22c55e",
        ),
        "bug_hunter": Badge(
            name="bug_hunter",
            display_name="Bug Hunter",
            description="Found and reported bugs",
            icon="🐛",
            color="#84cc16",
        ),
        "contributor": Badge(
            name="contributor",
            display_name="Contributor",
            description="Contributed to Plexichat",
            icon="🔧",
            color="#3b82f6",
        ),
        "moderator": Badge(
            name="moderator",
            display_name="Moderator",
            description="Community moderator",
            icon="🛡️",
            color="#8b5cf6",
        ),
        "partner": Badge(
            name="partner",
            display_name="Partner",
            description="Plexichat partner",
            icon="🤝",
            color="#ec4899",
        ),
    }


# The catalogue only changes when the config source does, so it is cached
# against the live loader identity + config file mtime/size (see
# ``config_source_version``).  A short staleness ceiling keeps the hot path
# free of even the version-check stat for five seconds at a time, while an
# explicit invalidate remains available for reload/admin paths.
_BADGE_CATALOG_MAX_AGE_SECONDS = 5.0

_badge_catalog_snapshot = VersionedConfigSnapshot(
    _build_badge_catalog,
    version_fn=lambda: config_source_version(repr(AVAILABLE_BADGES)),
    max_age=_BADGE_CATALOG_MAX_AGE_SECONDS,
)


def invalidate_badge_catalog_cache() -> None:
    """Drop the cached badge catalogue so the next access rebuilds it."""
    _badge_catalog_snapshot.invalidate()


@dataclass
class TierLimits:
    """Rate limit tier configuration."""

    name: str
    multiplier: float = 1.0

    # Voice/Video limits (-1 = unlimited)
    max_voice_minutes_per_day: int = 120
    max_video_minutes_per_day: int = 60

    # File limits
    max_file_uploads_per_day: int = 50
    max_file_size_mb: int = 10
    # Total retained storage for this tier. -1 means unlimited.
    max_storage_mb: int = 5 * 1024

    # Server limits
    max_servers: int = 100

    # Message limits
    max_message_length: int = 2000
    max_reactions_per_message: int = 20
    max_pins_per_channel: int = 50

    # Emoji limits
    custom_emoji_slots: int = 50

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "name": self.name,
            "multiplier": self.multiplier,
            "max_voice_minutes_per_day": self.max_voice_minutes_per_day,
            "max_video_minutes_per_day": self.max_video_minutes_per_day,
            "max_file_uploads_per_day": self.max_file_uploads_per_day,
            "max_file_size_mb": self.max_file_size_mb,
            "max_storage_mb": self.max_storage_mb,
            "max_servers": self.max_servers,
            "max_message_length": self.max_message_length,
            "max_reactions_per_message": self.max_reactions_per_message,
            "max_pins_per_channel": self.max_pins_per_channel,
            "custom_emoji_slots": self.custom_emoji_slots,
        }


# Default tier configurations (used if not in config)
DEFAULT_TIER_LIMITS = {
    "standard": TierLimits(
        name="standard",
        multiplier=1.0,
        max_voice_minutes_per_day=120,
        max_video_minutes_per_day=60,
        max_file_uploads_per_day=50,
        max_file_size_mb=10,
        max_storage_mb=5 * 1024,
        max_servers=100,
    ),
    "alpha": TierLimits(
        name="alpha",
        multiplier=2.0,
        max_voice_minutes_per_day=480,
        max_video_minutes_per_day=240,
        max_file_uploads_per_day=200,
        max_file_size_mb=25,
        max_storage_mb=10 * 1024,
        max_servers=200,
    ),
    "premium": TierLimits(
        name="premium",
        multiplier=3.0,
        max_voice_minutes_per_day=-1,  # Unlimited
        max_video_minutes_per_day=-1,
        max_file_uploads_per_day=500,
        max_file_size_mb=100,
        max_storage_mb=50 * 1024,
        max_servers=500,
    ),
}


@dataclass
class UserFeatures:
    """User feature flags and badges."""

    id: SnowflakeID
    user_id: SnowflakeID

    # Rate limiting
    rate_limit_tier: str = "standard"

    # Badges (admin-controlled, displayed on profile)
    badges: List[str] = field(default_factory=list)

    # Metadata
    granted_by: Optional[SnowflakeID] = None  # Admin who granted
    granted_at: Optional[int] = None
    expires_at: Optional[int] = None  # None = permanent
    notes: Optional[str] = None

    def get_tier_limits(self) -> TierLimits:
        """Get the limits for this user's tier."""
        if self.rate_limit_tier in DEFAULT_TIER_LIMITS:
            return DEFAULT_TIER_LIMITS[self.rate_limit_tier]
        return DEFAULT_TIER_LIMITS["standard"]

    def get_badge_info(self) -> List[Badge]:
        """Get full badge info for all user badges."""
        return [Badge.get_badge_info(b) for b in self.badges]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "user_id": str(self.user_id),
            "rate_limit_tier": self.rate_limit_tier,
            "badges": self.badges,
            "expires_at": self.expires_at,
        }

    def to_public_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for public API responses (no admin info)."""
        return {
            "badges": self.badges,
            "tier": self.rate_limit_tier,
        }
