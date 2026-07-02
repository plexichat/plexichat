from typing import Dict, List, Optional

from .base import SearchManagerBase


class AccessControlMixin(SearchManagerBase):
    def _can_access_server(self, user_id: int, server_id: int) -> bool:
        try:
            member = self._db.fetch_one(
                "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
                (server_id, user_id),
            )
            return member is not None
        except Exception:
            return False

    def _can_access_conversation(self, user_id: int, conversation_id: int) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return row is not None

    def _can_access_channel(self, user_id: int, channel_id: int) -> bool:
        if not self._servers:
            return True

        try:
            channel = self._db.fetch_one(
                "SELECT server_id FROM srv_channels WHERE id = ?", (channel_id,)
            )
            if not channel:
                return False

            return self._servers.has_permission(
                user_id,
                channel["server_id"],
                "messages.read",
                channel_id,
            )
        except Exception:
            return False

    def _get_accessible_conversations(
        self,
        user_id: int,
        conversation_id: Optional[int] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
    ) -> List[int]:
        if conversation_id:
            if self._can_access_conversation(user_id, conversation_id):
                return [conversation_id]
            return []

        if channel_id:
            try:
                channel = self._db.fetch_one(
                    "SELECT conversation_id FROM srv_channels WHERE id = ?",
                    (channel_id,),
                )
                if channel and self._can_access_channel(user_id, channel_id):
                    return [channel["conversation_id"]]
            except Exception:
                pass
            return []

        conversations = []

        dm_convs = self._db.fetch_all(
            """SELECT conversation_id FROM msg_participants 
               WHERE user_id = ?""",
            (user_id,),
        )
        conversations.extend(row["conversation_id"] for row in dm_convs)

        if server_id:
            try:
                rows = self._db.fetch_all(
                    "SELECT conversation_id FROM srv_channels WHERE server_id = ? AND conversation_id IS NOT NULL",
                    (server_id,),
                )
                conversations.extend(row["conversation_id"] for row in rows)
            except Exception:
                pass
        else:
            try:
                rows = self._db.fetch_all(
                    """SELECT c.conversation_id 
                       FROM srv_channels c 
                       JOIN srv_members m ON c.server_id = m.server_id 
                       WHERE m.user_id = ? AND c.conversation_id IS NOT NULL""",
                    (user_id,),
                )
                conversations.extend(row["conversation_id"] for row in rows)
            except Exception:
                pass

        return list(set(conversations))

    def _get_server_member_ids(self, server_id: int) -> set:
        try:
            rows = self._db.fetch_all(
                "SELECT user_id FROM srv_members WHERE server_id = ?", (server_id,)
            )
            return {row["user_id"] for row in rows}
        except Exception:
            return set()

    def _get_user_server_ids(self, user_id: int) -> set:
        from src.core.database import cache_get, cache_set, redis_available

        cache_key = f"user:servers:{int(user_id)}"
        if redis_available():
            cached = cache_get(cache_key)
            if isinstance(cached, list):
                return {int(x) for x in cached}
        rows = self._db.fetch_all(
            "SELECT server_id FROM srv_members WHERE user_id = ?", (user_id,)
        )
        server_ids = [int(row["server_id"]) for row in rows]
        if redis_available():
            cache_set(cache_key, server_ids, ttl=300)
        return set(server_ids)

    def _get_user_servers_map(self, user_ids: List[int]) -> Dict[int, set]:
        from src.core.database import cache_get, cache_set, redis_available

        uniq = sorted({int(uid) for uid in user_ids if uid})
        result: Dict[int, set] = {}
        missing: List[int] = []
        if redis_available():
            for uid in uniq:
                cached = cache_get(f"user:servers:{uid}")
                if isinstance(cached, list):
                    result[uid] = {int(x) for x in cached}
                else:
                    missing.append(uid)
        else:
            missing = uniq
        if missing:
            member_rows = []
            for uid in missing:
                rows = self._db.fetch_all(
                    "SELECT user_id, server_id FROM srv_members WHERE user_id = ?",
                    (uid,),
                )
                member_rows.extend(rows)
            for row in member_rows:
                uid = int(row["user_id"])
                sid = int(row["server_id"])
                if uid not in result:
                    result[uid] = set()
                result[uid].add(sid)
        if redis_available():
            for uid in missing:
                cache_set(f"user:servers:{uid}", list(result.get(uid, set())), ttl=300)
        return result
