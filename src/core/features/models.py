"""
User Features data models.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from src.core.base import SnowflakeID


# Available badge types
AVAILABLE_BADGES = [
    "alpha_tester",  # Alpha testing participant
    "early_supporter",  # Early adopter
    "staff",  # PlexiChat staff member
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
        """Get display info for a badge."""
        badges = {
            "alpha_tester": Badge(
                name="alpha_tester",
                display_name="Alpha Tester",
                description="Participated in PlexiChat alpha testing",
                icon="🧪",
                color="#9333ea",
            ),
            "early_supporter": Badge(
                name="early_supporter",
                display_name="Early Supporter",
                description="Supported PlexiChat early on",
                icon="💎",
                color="#06b6d4",
            ),
            "staff": Badge(
                name="staff",
                display_name="Staff",
                description="PlexiChat team member",
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
                description="Contributed to PlexiChat",
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
                description="PlexiChat partner",
                icon="🤝",
                color="#ec4899",
            ),
        }
        return badges.get(
            badge_name,
            Badge(
                name=badge_name,
                display_name=badge_name.replace("_", " ").title(),
                description="",
                icon="🏷️",
                color="#6b7280",
            ),
        )


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
        max_servers=100,
    ),
    "alpha": TierLimits(
        name="alpha",
        multiplier=2.0,
        max_voice_minutes_per_day=480,
        max_video_minutes_per_day=240,
        max_file_uploads_per_day=200,
        max_file_size_mb=25,
        max_servers=200,
    ),
    "premium": TierLimits(
        name="premium",
        multiplier=3.0,
        max_voice_minutes_per_day=-1,  # Unlimited
        max_video_minutes_per_day=-1,
        max_file_uploads_per_day=500,
        max_file_size_mb=100,
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
