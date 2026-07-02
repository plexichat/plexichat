"""Presence query and focus operations mixin.

Handles fetching full presence information (including joined custom status
and activity), batch presence queries, last-seen updates, and transient
focused-channel state in Redis.
"""

from typing import Any, Dict, List, Optional, cast

import utils.logger as logger
from src.core.database import (
    cache_set,
    cache_delete,
    redis_available,
    get_cached_presence,
    get_redis_client,
)

from .base import PresenceManagerBase
from ..models import Presence, UserStatus, CustomStatus, Activity, ActivityType


class PresenceMixin(PresenceManagerBase):
    """Mixin providing presence query, batch, and focus operations."""

    def get_presence(self, user_id: int, use_cache: bool = True) -> Presence:
        """Get full presence information for a user with optimized database access."""
        if use_cache and redis_available():
            cached = get_cached_presence(user_id)
            if cached:
                return self._dict_to_presence(cached)

        query = """
            SELECT
                p.status, p.last_seen, p.updated_at,
                cs.text as cs_text, cs.emoji as cs_emoji, cs.expires_at as cs_expires, cs.created_at as cs_created,
                a.activity_type, a.name as act_name, a.details as act_details, a.url as act_url,
                a.state as act_state, a.start_timestamp, a.end_timestamp,
                a.large_image, a.large_text, a.small_image, a.small_text
            FROM pres_presence p
            LEFT JOIN pres_custom_status cs ON p.user_id = cs.user_id
            LEFT JOIN pres_activity a ON p.user_id = a.user_id
            WHERE p.user_id = ?
        """
        row = self._db.fetch_one(query, (user_id,))

        if not row:
            presence = Presence(
                user_id=user_id,
                status=UserStatus.OFFLINE,
                custom_status=None,
                activity=None,
                last_seen=0,
                updated_at=0,
            )
        else:
            custom_status = None
            if row["cs_text"]:
                custom_status = CustomStatus(
                    text=row["cs_text"],
                    emoji=row["cs_emoji"],
                    expires_at=row["cs_expires"],
                    created_at=row["cs_created"],
                )

            activity = None
            if row["act_name"]:
                activity = Activity(
                    activity_type=ActivityType(row["activity_type"]),
                    name=row["act_name"],
                    details=row["act_details"],
                    url=row["act_url"],
                    state=row["act_state"],
                    start_timestamp=row["start_timestamp"],
                    end_timestamp=row["end_timestamp"],
                    large_image=row["large_image"],
                    large_text=row["large_text"],
                    small_image=row["small_image"],
                    small_text=row["small_text"],
                )

            presence = Presence(
                user_id=user_id,
                status=UserStatus(row["status"]),
                custom_status=custom_status,
                activity=activity,
                last_seen=row["last_seen"],
                updated_at=row["updated_at"],
            )

        if redis_available():
            client = get_redis_client()
            if client:
                try:
                    focus = client.hgetall(f"presence:focus:{user_id}")
                    if focus:
                        focus_data = cast(Dict[Any, Any], focus)
                        channel_value = focus_data.get("channel_id") or focus_data.get(
                            b"channel_id"
                        )
                        server_value = focus_data.get("server_id") or focus_data.get(
                            b"server_id"
                        )
                        if channel_value is not None:
                            presence.current_channel_id = int(channel_value) or None
                        if server_value is not None:
                            presence.current_server_id = int(server_value) or None
                except Exception:
                    pass

        if use_cache and redis_available() and row:
            try:
                cache_set(
                    f"presence:{user_id}",
                    self._presence_to_dict(presence),
                    ttl=self._presence_timeout_ms // 1000,
                )
            except Exception as e:
                logger.debug(f"Failed to cache presence for user {user_id}: {e}")

        return presence

    def get_presences(self, user_ids: List[int]) -> List[Presence]:
        """Get presence information for multiple users efficiently with batch queries and Redis caching."""
        if not user_ids:
            return []

        results_map: Dict[int, Presence] = {}
        missing_ids = list(user_ids)

        if redis_available():
            try:
                from src.core.database.cache import get_bulk_presence

                cached_data = get_bulk_presence(user_ids)
                for uid, data in cached_data.items():
                    try:
                        results_map[uid] = self._dict_to_presence(data)
                        missing_ids.remove(uid)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Failed to fetch bulk presence from Redis: {e}")

        if not missing_ids:
            return [results_map[uid] for uid in user_ids]

        placeholders = ",".join("?" * len(missing_ids))
        presence_rows = self._db.fetch_all(
            f"SELECT * FROM pres_presence WHERE user_id IN ({placeholders})",
            tuple(missing_ids),
        )
        presence_map = {row["user_id"]: row for row in presence_rows}

        self._cleanup_expired_custom_status_batch(missing_ids)
        custom_rows = self._db.fetch_all(
            f"SELECT * FROM pres_custom_status WHERE user_id IN ({placeholders})",
            tuple(missing_ids),
        )
        custom_map = {row["user_id"]: row for row in custom_rows}

        activity_rows = self._db.fetch_all(
            f"SELECT * FROM pres_activity WHERE user_id IN ({placeholders})",
            tuple(missing_ids),
        )
        activity_map = {row["user_id"]: row for row in activity_rows}

        for uid in missing_ids:
            pres_row = presence_map.get(uid)
            if not pres_row:
                presence = Presence(
                    user_id=uid,
                    status=UserStatus.OFFLINE,
                    custom_status=None,
                    activity=None,
                    last_seen=0,
                    updated_at=0,
                )
            else:
                cust_row = custom_map.get(uid)
                custom_status = (
                    CustomStatus(
                        text=cust_row["text"],
                        emoji=cust_row["emoji"],
                        expires_at=cust_row["expires_at"],
                    )
                    if cust_row
                    else None
                )

                act_row = activity_map.get(uid)
                activity = (
                    Activity(
                        activity_type=ActivityType(act_row["activity_type"]),
                        name=act_row["name"],
                        details=act_row["details"],
                        url=act_row["url"],
                        state=act_row["state"],
                        start_timestamp=act_row["start_timestamp"],
                        end_timestamp=act_row["end_timestamp"],
                        large_image=act_row["large_image"],
                        large_text=act_row["large_text"],
                        small_image=act_row["small_image"],
                        small_text=act_row["small_text"],
                        created_at=act_row["created_at"],
                    )
                    if act_row
                    else None
                )

                presence = Presence(
                    user_id=uid,
                    status=UserStatus(pres_row["status"]),
                    custom_status=custom_status,
                    activity=activity,
                    last_seen=pres_row["last_seen"],
                    updated_at=pres_row["updated_at"],
                )

            results_map[uid] = presence

            if redis_available() and pres_row:
                cache_set(
                    f"presence:{uid}",
                    self._presence_to_dict(presence),
                    ttl=self._presence_timeout_ms // 1000,
                )

        return [results_map[uid] for uid in user_ids]

    def _cleanup_expired_custom_status_batch(self, user_ids: List[int]) -> None:
        """Remove expired custom statuses for multiple users."""
        if not user_ids:
            return
        now = self._get_timestamp()
        placeholders = ",".join("?" * len(user_ids))
        self._db.execute(
            f"DELETE FROM pres_custom_status WHERE user_id IN ({placeholders}) AND expires_at IS NOT NULL AND expires_at < ?",
            tuple(user_ids) + (now,),
        )

    def update_last_seen(self, user_id: int) -> Presence:
        """Update user's last seen timestamp."""
        self._validate_user(user_id)
        self._ensure_presence_record(user_id)

        now = self._get_timestamp()

        self._db.execute(
            "UPDATE pres_presence SET last_seen = ?, updated_at = ? WHERE user_id = ?",
            (now, now, user_id),
        )

        result = self.get_presence(user_id)
        return result

    def set_focused_channel(
        self,
        user_id: int,
        channel_id: Optional[int] = None,
        server_id: Optional[int] = None,
    ) -> bool:
        """
        Set user's currently focused channel/server for notification suppression.
        This is transient state stored ONLY in Redis.
        """
        if not redis_available():
            return False

        client = get_redis_client()
        if not client:
            return False

        key = f"presence:focus:{user_id}"
        try:
            if channel_id is None:
                client.delete(key)
            else:
                client.hset(key, "channel_id", str(channel_id))
                client.hset(key, "server_id", str(server_id or 0))
                client.expire(key, 3600)

            cache_delete(f"presence:{user_id}")
            return True
        except Exception as e:
            logger.debug(f"Failed to set focused channel in Redis: {e}")
            return False
