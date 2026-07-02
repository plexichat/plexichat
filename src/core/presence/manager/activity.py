"""Activity operations mixin.

Handles setting, getting, and clearing user activity information
such as games, music, or streaming status.
"""

from typing import Optional, Dict, Union

import utils.logger as logger
from src.core.database import (
    cache_set,
    redis_available,
)

from .base import PresenceManagerBase
from ..models import Presence, Activity, ActivityType
from ..exceptions import InvalidActivityError


class ActivityMixin(PresenceManagerBase):
    """Mixin providing activity operations."""

    def set_activity(
        self,
        user_id: int,
        activity_type: Union[ActivityType, str],
        name: str,
        details: Optional[str] = None,
        url: Optional[str] = None,
        state: Optional[str] = None,
        timestamps: Optional[Dict[str, int]] = None,
        assets: Optional[Dict[str, str]] = None,
    ) -> Presence:
        """
        Set user's current activity.

        Args:
            user_id: ID of the user
            activity_type: Type of activity
            name: Activity name (e.g., game name, song title)
            details: Optional details
            url: Optional URL (for streaming)
            state: Optional state text
            timestamps: Optional dict with 'start' and/or 'end' timestamps
            assets: Optional dict with image keys (large_image, large_text, etc.)

        Returns:
            Updated Presence
        """
        if isinstance(activity_type, str):
            try:
                activity_type = ActivityType(activity_type.lower())
            except ValueError:
                activity_type = ActivityType.PLAYING

        self._validate_user(user_id)
        self._ensure_presence_record(user_id)

        if not name or not name.strip():
            raise InvalidActivityError("Activity name cannot be empty")

        now = self._get_timestamp()

        start_ts = timestamps.get("start") if timestamps else None
        end_ts = timestamps.get("end") if timestamps else None
        large_image = assets.get("large_image") if assets else None
        large_text = assets.get("large_text") if assets else None
        small_image = assets.get("small_image") if assets else None
        small_text = assets.get("small_text") if assets else None

        self._db.upsert(
            "pres_activity",
            [
                "user_id",
                "activity_type",
                "name",
                "details",
                "url",
                "state",
                "start_timestamp",
                "end_timestamp",
                "large_image",
                "large_text",
                "small_image",
                "small_text",
                "created_at",
            ],
            (
                user_id,
                activity_type.value,
                name,
                details,
                url,
                state,
                start_ts,
                end_ts,
                large_image,
                large_text,
                small_image,
                small_text,
                now,
            ),
            ["user_id"],
            [
                "activity_type",
                "name",
                "details",
                "url",
                "state",
                "start_timestamp",
                "end_timestamp",
                "large_image",
                "large_text",
                "small_image",
                "small_text",
            ],
        )

        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?", (now, user_id)
        )

        if redis_available():
            presence = self.get_presence(user_id, use_cache=False)
            cache_set(
                f"presence:{user_id}",
                self._presence_to_dict(presence),
                ttl=self._presence_timeout_ms // 1000,
            )

        logger.debug(f"User {user_id} activity set to {activity_type.value}: {name}")

        result = self.get_presence(user_id)
        return result

    def get_activity(self, user_id: int) -> Optional[Activity]:
        """Get user's current activity."""
        row = self._db.fetch_one(
            "SELECT * FROM pres_activity WHERE user_id = ?", (user_id,)
        )

        if not row:
            return None

        return Activity(
            activity_type=ActivityType(row["activity_type"]),
            name=row["name"],
            details=row["details"],
            url=row["url"],
            state=row["state"],
            start_timestamp=row["start_timestamp"],
            end_timestamp=row["end_timestamp"],
            large_image=row["large_image"],
            large_text=row["large_text"],
            small_image=row["small_image"],
            small_text=row["small_text"],
            created_at=row["created_at"],
        )

    def clear_activity(self, user_id: int) -> Presence:
        """Clear user's current activity."""
        self._validate_user(user_id)

        self._db.execute("DELETE FROM pres_activity WHERE user_id = ?", (user_id,))

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?", (now, user_id)
        )

        if redis_available():
            presence = self.get_presence(user_id, use_cache=False)
            cache_set(
                f"presence:{user_id}",
                self._presence_to_dict(presence),
                ttl=self._presence_timeout_ms // 1000,
            )

        result = self.get_presence(user_id)
        return result
