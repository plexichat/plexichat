"""Base class for PresenceManager mixins.

Declares typed attributes so pyright can resolve self._db, self._relationships,
etc. across all mixin files without suppression comments.
"""

from typing import Any, Dict, List

from src.core.base import BaseManager, SnowflakeID

from ..models import (
    Presence,
    UserStatus,
    Activity,
    ActivityType,
    TypingIndicator,
    CustomStatus,
)
from ..exceptions import (
    UserNotFoundError,
)
import utils.config as config
import utils.logger as logger


class PresenceManagerBase(BaseManager):
    """Base class providing typed access to shared state and utility methods."""

    _relationships: Any
    _servers: Any
    _config: Dict[str, Any]
    _typing_timeout_ms: int
    _presence_timeout_ms: int

    UserStatus = UserStatus
    ActivityType = ActivityType
    Presence = Presence
    Activity = Activity
    TypingIndicator = TypingIndicator
    CustomStatus = CustomStatus

    def get_presence(self, user_id: int, use_cache: bool = True) -> Presence:
        """Get full presence information. Overridden by PresenceMixin."""
        raise NotImplementedError

    def get_presences(self, user_ids: List[int]) -> List[Presence]:
        """Get presence for multiple users. Overridden by PresenceMixin."""
        raise NotImplementedError

    def get_status(self, user_id: int) -> UserStatus:
        """Get user status. Overridden by StatusMixin."""
        raise NotImplementedError

    def __init__(
        self, db, auth_module=None, relationships_module=None, servers_module=None
    ):
        super().__init__(db, auth_module)
        self._relationships = relationships_module
        self._servers = servers_module
        self._config = self._load_config()

        self._typing_timeout_ms = self._config.get("typing_timeout_ms", 6000)
        self._presence_timeout_ms = self._config.get("timeout_ms", 300000)

        logger.info("Presence module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load presence configuration."""
        return config.get("presence", {})

    def _validate_user(self, user_id: SnowflakeID) -> None:
        """Validate user exists."""
        if not self._user_exists(user_id):
            raise UserNotFoundError(f"User {user_id} not found")

    def _ensure_presence_record(self, user_id: SnowflakeID) -> None:
        """Ensure a presence record exists for user."""
        now = self._get_timestamp()
        self._db.insert_or_ignore(
            "pres_presence",
            ["user_id", "status", "last_seen", "updated_at"],
            (user_id, "offline", now, now),
        )

    def _presence_to_dict(self, presence: Presence) -> Dict[str, Any]:
        """Convert Presence model to dict for caching."""
        return {
            "user_id": presence.user_id,
            "status": presence.status.value,
            "custom_status": {
                "text": presence.custom_status.text,
                "emoji": presence.custom_status.emoji,
                "expires_at": presence.custom_status.expires_at,
            }
            if presence.custom_status
            else None,
            "activity": {
                "activity_type": presence.activity.activity_type.value,
                "name": presence.activity.name,
                "details": presence.activity.details,
                "url": presence.activity.url,
                "state": presence.activity.state,
                "start_timestamp": presence.activity.start_timestamp,
                "end_timestamp": presence.activity.end_timestamp,
                "large_image": presence.activity.large_image,
                "large_text": presence.activity.large_text,
                "small_image": presence.activity.small_image,
                "small_text": presence.activity.small_text,
                "created_at": presence.activity.created_at,
            }
            if presence.activity
            else None,
            "last_seen": presence.last_seen,
            "updated_at": presence.updated_at,
            "current_channel_id": presence.current_channel_id,
            "current_server_id": presence.current_server_id,
        }

    def _dict_to_presence(self, data: Dict[str, Any]) -> Presence:
        """Convert cached dict to Presence model."""
        custom_status = data.get("custom_status")
        activity = data.get("activity")

        return Presence(
            user_id=data["user_id"],
            status=UserStatus(data["status"]),
            custom_status=CustomStatus(
                text=custom_status["text"],
                emoji=custom_status["emoji"],
                expires_at=custom_status["expires_at"],
            )
            if custom_status
            else None,
            activity=Activity(
                activity_type=ActivityType(activity["activity_type"]),
                name=activity["name"],
                details=activity.get("details"),
                url=activity.get("url"),
                state=activity.get("state"),
                start_timestamp=activity.get("start_timestamp"),
                end_timestamp=activity.get("end_timestamp"),
                large_image=activity.get("large_image"),
                large_text=activity.get("large_text"),
                small_image=activity.get("small_image"),
                small_text=activity.get("small_text"),
                created_at=activity.get("created_at", 0),
            )
            if activity
            else None,
            last_seen=data.get("last_seen", 0),
            updated_at=data.get("updated_at", 0),
            current_channel_id=data.get("current_channel_id"),
            current_server_id=data.get("current_server_id"),
        )
