"""Activity operations mixin.

Handles setting, getting, and clearing user activity information
such as games, music, or streaming status.
"""

from typing import Optional, Dict, Union

import utils.logger as logger
from src.core.database import (
    cache_set,
)
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    reject_control_characters,
    require_http_url,
)

from .base import PresenceManagerBase
from ..models import Presence, Activity, ActivityType
from ..exceptions import InvalidActivityError

#: Field ceilings for activity payloads, which are broadcast to other users.
_MAX_ACTIVITY_ASSET_LENGTH = 256
_MAX_TIMESTAMP = 2**63 - 1


def _activity_text(value: object, field: str, maximum: int) -> str:
    """Return a trimmed, control-safe, length-capped activity string.

    Activity text is rendered to every viewer of a presence, so a CR/LF could
    forge a log line or a WebSocket frame in a downstream client. Unlike the
    legacy ``value[:n]`` truncation this refuses control characters outright.
    """
    text = reject_control_characters(str(value or "").strip(), field)
    return text[:maximum]


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
                raise InvalidActivityError(
                    f"Invalid activity type: {activity_type}"
                ) from None

        self._validate_user(user_id)
        self._ensure_presence_record(user_id)

        if not name or not name.strip():
            raise InvalidActivityError("Activity name cannot be empty")

        try:
            # Every field below is broadcast to other users as part of the
            # presence payload. Refuse control characters so a hostile name /
            # detail / asset cannot forge a log line or a downstream frame.
            name = _activity_text(name, "activity name", 128)
            if not name:
                raise InvalidActivityError("Activity name cannot be empty")
            if details is not None:
                details = _activity_text(details, "activity details", 1024)
            if state is not None:
                state = _activity_text(state, "activity state", 1024)
            if url:
                # The old startswith() check let embedded credentials through
                # (https://user:pass@host) and only looked at the scheme
                # prefix. Use the shared absolute-http(s) contract instead.
                url = require_http_url(
                    reject_control_characters(str(url), "activity url"),
                    "activity url",
                )[:1024]

            def _timestamp(value: object, field: str) -> Optional[int]:
                if value is None:
                    return None
                return bounded_int(value, field, minimum=0, maximum=_MAX_TIMESTAMP)

            def _asset(value: object, field: str) -> Optional[str]:
                if value is None:
                    return None
                return _activity_text(value, field, _MAX_ACTIVITY_ASSET_LENGTH) or None

            start_ts = _timestamp(
                timestamps.get("start") if timestamps else None,
                "activity start timestamp",
            )
            end_ts = _timestamp(
                timestamps.get("end") if timestamps else None,
                "activity end timestamp",
            )
            large_image = _asset(
                assets.get("large_image") if assets else None, "activity large_image"
            )
            large_text = _asset(
                assets.get("large_text") if assets else None, "activity large_text"
            )
            small_image = _asset(
                assets.get("small_image") if assets else None, "activity small_image"
            )
            small_text = _asset(
                assets.get("small_text") if assets else None, "activity small_text"
            )
        except HardeningViolation as exc:
            raise InvalidActivityError(f"Invalid activity field: {exc}") from exc

        now = self._get_timestamp()

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

        try:
            presence = self.get_presence(user_id, use_cache=False)
            cache_set(
                f"presence:{user_id}",
                self._presence_to_dict(presence),
                ttl=self._presence_timeout_ms // 1000,
            )
        except Exception as e:
            logger.debug(f"Failed to cache presence for user {user_id}: {e}")

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

        try:
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
        except ValueError:
            return None

    def clear_activity(self, user_id: int) -> Presence:
        """Clear user's current activity."""
        self._validate_user(user_id)

        self._db.execute("DELETE FROM pres_activity WHERE user_id = ?", (user_id,))

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?", (now, user_id)
        )

        try:
            presence = self.get_presence(user_id, use_cache=False)
            cache_set(
                f"presence:{user_id}",
                self._presence_to_dict(presence),
                ttl=self._presence_timeout_ms // 1000,
            )
        except Exception as e:
            logger.debug(f"Failed to cache presence for user {user_id}: {e}")

        result = self.get_presence(user_id)
        return result
