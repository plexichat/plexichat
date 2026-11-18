from typing import Optional, List, Dict

from src.core.base import SnowflakeID
from src.core.database import cache_get, cache_set, cache_delete, valkey_available
from ..models import (
    NotificationSettings,
    ChannelNotificationOverride,
    NotificationLevel,
)
from .helpers import row_to_settings, row_to_channel_override


from .protocol import NotificationProtocol


# The recipient list for these helpers comes from mention fan-out and is not
# bounded here: one ``@everyone``/role mention in a large server can yield tens
# of thousands of ids. Building a single ``IN (?,?,...)`` with one bind
# variable per id crosses SQLite's bind-variable cap (999 on older builds,
# 32766 on newer ones) and raises "too many SQL variables", turning a
# legitimate mention into a crashed send path. Chunk well under both limits
# (Postgres allows ~32k but chunking costs nothing) — the same pattern the
# search indexer uses for its bulk id lookups.
_BULK_ID_CHUNK_SIZE = 500


def _id_chunks(items: List[SnowflakeID]):
    """Yield successive bounded slices of ``items`` for IN-clause binding."""
    for start in range(0, len(items), _BULK_ID_CHUNK_SIZE):
        yield items[start : start + _BULK_ID_CHUNK_SIZE]


class SettingsMixin(NotificationProtocol):
    def get_notification_settings_bulk(
        self, user_ids: List[SnowflakeID], server_id: Optional[SnowflakeID] = None
    ) -> Dict[int, NotificationSettings]:
        if not user_ids:
            return {}

        # Materialise once so every chunk binds from a stable, ordered list
        # (callers may pass a set or other iterable).
        ids = list(user_ids)
        results: Dict[int, NotificationSettings] = {}
        for chunk in _id_chunks(ids):
            placeholders = ",".join(["?"] * len(chunk))
            if server_id:
                rows = self._db.fetch_all(
                    f"SELECT * FROM notif_settings WHERE user_id IN ({placeholders}) AND server_id = ?",
                    (*chunk, server_id),
                )
            else:
                rows = self._db.fetch_all(
                    f"SELECT * FROM notif_settings WHERE user_id IN ({placeholders}) AND server_id IS NULL",
                    tuple(chunk),
                )
            for row in rows:
                results[row["user_id"]] = row_to_settings(row)

        for uid in ids:
            if uid not in results:
                results[uid] = NotificationSettings(
                    user_id=uid,
                    server_id=server_id,
                    level=NotificationLevel.ALL_MESSAGES,
                    dm_notifications=True,
                    suppress_everyone=False,
                    suppress_roles=False,
                    mobile_push=True,
                )
        return results

    def get_channel_overrides_bulk(
        self, user_ids: List[SnowflakeID], channel_id: SnowflakeID
    ) -> Dict[int, ChannelNotificationOverride]:
        if not user_ids:
            return {}

        results: Dict[int, ChannelNotificationOverride] = {}
        for chunk in _id_chunks(list(user_ids)):
            placeholders = ",".join(["?"] * len(chunk))
            rows = self._db.fetch_all(
                f"SELECT * FROM notif_channel_overrides WHERE channel_id = ? AND user_id IN ({placeholders})",
                (channel_id, *chunk),
            )
            for row in rows:
                results[row["user_id"]] = row_to_channel_override(row)

        return results

    def get_notification_settings(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None
    ) -> NotificationSettings:
        cache_key = f"notif_settings:{user_id}:{server_id if server_id is not None else 'global'}"
        if valkey_available():
            cached = cache_get(cache_key)
            if cached:
                return NotificationSettings(
                    user_id=cached["user_id"],
                    server_id=cached.get("server_id"),
                    level=NotificationLevel(cached["level"]),
                    dm_notifications=cached["dm_notifications"],
                    suppress_everyone=cached["suppress_everyone"],
                    suppress_roles=cached["suppress_roles"],
                    mobile_push=cached["mobile_push"],
                )

        if server_id is not None:
            row = self._db.fetch_one(
                "SELECT * FROM notif_settings WHERE user_id = ? AND server_id = ?",
                (user_id, server_id),
            )
        else:
            row = self._db.fetch_one(
                "SELECT * FROM notif_settings WHERE user_id = ? AND server_id IS NULL",
                (user_id,),
            )

        if row:
            settings = row_to_settings(row)
        else:
            settings = NotificationSettings(
                user_id=user_id,
                server_id=server_id,
                level=NotificationLevel.ALL_MESSAGES,
                dm_notifications=True,
                suppress_everyone=False,
                suppress_roles=False,
                mobile_push=True,
            )

        if valkey_available():
            cache_set(
                cache_key,
                {
                    "user_id": settings.user_id,
                    "server_id": settings.server_id,
                    "level": settings.level.value,
                    "dm_notifications": settings.dm_notifications,
                    "suppress_everyone": settings.suppress_everyone,
                    "suppress_roles": settings.suppress_roles,
                    "mobile_push": settings.mobile_push,
                },
                ttl=60,
            )

        return settings

    def update_notification_settings(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None, **kwargs
    ) -> NotificationSettings:
        now = self._get_timestamp()

        if server_id is not None:
            existing = self._db.fetch_one(
                "SELECT id FROM notif_settings WHERE user_id = ? AND server_id = ?",
                (user_id, server_id),
            )
        else:
            existing = self._db.fetch_one(
                "SELECT id FROM notif_settings WHERE user_id = ? AND server_id IS NULL",
                (user_id,),
            )

        level = kwargs.get("level", NotificationLevel.ALL_MESSAGES)
        if isinstance(level, str):
            level = NotificationLevel(level)

        dm_notifications = kwargs.get("dm_notifications", True)
        suppress_everyone = kwargs.get("suppress_everyone", False)
        suppress_roles = kwargs.get("suppress_roles", False)
        mobile_push = kwargs.get("mobile_push", True)

        if existing:
            self._db.execute(
                """UPDATE notif_settings SET
                   level = ?, dm_notifications = ?, suppress_everyone = ?,
                   suppress_roles = ?, mobile_push = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    level.value,
                    1 if dm_notifications else 0,
                    1 if suppress_everyone else 0,
                    1 if suppress_roles else 0,
                    1 if mobile_push else 0,
                    now,
                    existing["id"],
                ),
            )
        else:
            settings_id = self._generate_id()
            self._db.execute(
                """INSERT INTO notif_settings
                   (id, user_id, server_id, level, dm_notifications, suppress_everyone,
                    suppress_roles, mobile_push, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    settings_id,
                    user_id,
                    server_id,
                    level.value,
                    1 if dm_notifications else 0,
                    1 if suppress_everyone else 0,
                    1 if suppress_roles else 0,
                    1 if mobile_push else 0,
                    now,
                    now,
                ),
            )

        cache_key = f"notif_settings:{user_id}:{server_id if server_id is not None else 'global'}"
        if valkey_available():
            cache_delete(cache_key)

        return self.get_notification_settings(user_id, server_id)

    def get_channel_override(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> Optional[ChannelNotificationOverride]:
        row = self._db.fetch_one(
            "SELECT * FROM notif_channel_overrides WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        if not row:
            return None

        return row_to_channel_override(row)

    def set_channel_override(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        level: NotificationLevel,
        muted_until: Optional[int] = None,
    ) -> ChannelNotificationOverride:
        now = self._get_timestamp()

        if isinstance(level, str):
            level = NotificationLevel(level)

        existing = self._db.fetch_one(
            "SELECT id FROM notif_channel_overrides WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        if existing:
            self._db.execute(
                """UPDATE notif_channel_overrides SET
                   level = ?, muted_until = ?, updated_at = ?
                   WHERE id = ?""",
                (level.value, muted_until, now, existing["id"]),
            )
        else:
            override_id = self._generate_id()
            self._db.execute(
                """INSERT INTO notif_channel_overrides
                   (id, user_id, channel_id, level, muted_until, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (override_id, user_id, channel_id, level.value, muted_until, now, now),
            )

        result = self.get_channel_override(user_id, channel_id)
        assert result is not None
        return result

    def delete_channel_override(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> bool:
        existing = self._db.fetch_one(
            "SELECT 1 FROM notif_channel_overrides WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        if not existing:
            return False

        self._db.execute(
            "DELETE FROM notif_channel_overrides WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        return True
