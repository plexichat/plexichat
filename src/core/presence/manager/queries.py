"""Online query operations mixin.

Handles queries for online friends and server members using Redis
set intersection for high-speed lookups with database fallback.
"""

from typing import List

import utils.logger as logger
from src.core.database import (
    redis_available,
    get_redis_client,
)

from .base import PresenceManagerBase
from ..models import UserStatus


class OnlineQueryMixin(PresenceManagerBase):
    """Mixin providing online friend and server member queries."""

    def get_online_friends(self, user_id: int) -> List[int]:
        """
        Get list of online friend user IDs.

        Args:
            user_id: ID of the user

        Returns:
            List of online friend user IDs
        """
        if not self._relationships:
            return []

        friend_ids = self._relationships.get_friend_ids(user_id)
        if not friend_ids:
            return []

        if redis_available():
            client = get_redis_client()
            if client:
                try:
                    online_set = client.smembers("presence:online_users")
                    online_ids = {int(uid) for uid in online_set}
                    return [fid for fid in friend_ids if fid in online_ids]
                except Exception as e:
                    logger.debug(f"Redis get_online_friends failed: {e}")

        online_statuses = [
            UserStatus.ONLINE.value,
            UserStatus.IDLE.value,
            UserStatus.DND.value,
        ]

        placeholders = ",".join("?" * len(friend_ids))
        status_placeholders = ",".join("?" * len(online_statuses))

        rows = self._db.fetch_all(
            f"""SELECT user_id FROM pres_presence
                WHERE user_id IN ({placeholders})
                AND status IN ({status_placeholders})""",
            tuple(friend_ids) + tuple(online_statuses),
        )

        return [row["user_id"] for row in rows]

    def get_online_server_members(self, user_id: int, server_id: int) -> List[int]:
        """
        Get list of online member user IDs in a server.

        Args:
            user_id: ID of the user making the request
            server_id: ID of the server

        Returns:
            List of online member user IDs
        """
        if not self._servers:
            return []

        if hasattr(self._servers, "get_member_user_ids"):
            member_ids = self._servers.get_member_user_ids(server_id)
        else:
            members = self._servers.get_members(user_id, server_id)
            if not members:
                return []
            member_ids = [m.user_id for m in members]

        if redis_available():
            client = get_redis_client()
            if client:
                try:
                    online_set = client.smembers("presence:online_users")
                    online_ids = {int(uid) for uid in online_set}
                    return [mid for mid in member_ids if mid in online_ids]
                except Exception as e:
                    logger.debug(f"Redis get_online_server_members failed: {e}")

        online_statuses = [
            UserStatus.ONLINE.value,
            UserStatus.IDLE.value,
            UserStatus.DND.value,
        ]

        placeholders = ",".join("?" * len(member_ids))
        status_placeholders = ",".join("?" * len(online_statuses))

        rows = self._db.fetch_all(
            f"""SELECT user_id FROM pres_presence
                WHERE user_id IN ({placeholders})
                AND status IN ({status_placeholders})""",
            tuple(member_ids) + tuple(online_statuses),
        )

        return [row["user_id"] for row in rows]
