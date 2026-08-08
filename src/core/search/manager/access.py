from typing import Dict, List, Optional

from .base import SearchManagerBase


class AccessControlMixin(SearchManagerBase):
    def _can_access_server(self, user_id: int, server_id: int) -> bool:
        member = self._db.fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        return member is not None

    def _can_access_conversation(self, user_id: int, conversation_id: int) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return row is not None

    def _can_access_channel(self, user_id: int, channel_id: int) -> bool:
        # A missing server/permission dependency is not proof of access.
        # Fail closed rather than allowing a search to bypass channel ACLs.
        if not self._servers:
            return False

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
            channel = self._db.fetch_one(
                "SELECT conversation_id FROM srv_channels WHERE id = ?",
                (channel_id,),
            )
            if channel and self._can_access_channel(user_id, channel_id):
                return [channel["conversation_id"]]
            return []

        conversations = []

        dm_convs = self._db.fetch_all(
            """SELECT conversation_id FROM msg_participants
               WHERE user_id = ?""",
            (user_id,),
        )
        conversations.extend(row["conversation_id"] for row in dm_convs)

        if server_id:
            rows = self._db.fetch_all(
                "SELECT id, conversation_id FROM srv_channels WHERE server_id = ? AND conversation_id IS NOT NULL",
                (server_id,),
            )
            for row in rows:
                if self._can_access_channel(user_id, row["id"]):
                    conversations.append(row["conversation_id"])
        else:
            rows = self._db.fetch_all(
                """SELECT c.conversation_id
                   FROM srv_channels c
                   JOIN srv_members m ON c.server_id = m.server_id
                   WHERE m.user_id = ? AND c.conversation_id IS NOT NULL""",
                (user_id,),
            )
            conversations.extend(row["conversation_id"] for row in rows)

        return list(set(conversations))

    def _get_server_member_ids(self, server_id: int) -> set:
        rows = self._db.fetch_all(
            "SELECT user_id FROM srv_members WHERE server_id = ?", (server_id,)
        )
        return {row["user_id"] for row in rows}

    def _get_user_server_ids(self, user_id: int) -> set:
        from src.core.database import cache_get, cache_set, valkey_available

        cache_key = f"user:servers:{int(user_id)}"
        if valkey_available():
            cached = cache_get(cache_key)
            if isinstance(cached, list):
                return {int(x) for x in cached}
        rows = self._db.fetch_all(
            "SELECT server_id FROM srv_members WHERE user_id = ?", (user_id,)
        )
        server_ids = [int(row["server_id"]) for row in rows]
        if valkey_available():
            cache_set(cache_key, server_ids, ttl=300)
        return set(server_ids)

    def _get_user_servers_map(self, user_ids: List[int]) -> Dict[int, set]:
        from src.core.database import cache_get, cache_set, valkey_available

        uniq = sorted({int(uid) for uid in user_ids if uid})
        result: Dict[int, set] = {}
        missing: List[int] = []
        if valkey_available():
            for uid in uniq:
                cached = cache_get(f"user:servers:{uid}")
                if isinstance(cached, list):
                    result[uid] = {int(x) for x in cached}
                else:
                    missing.append(uid)
        else:
            missing = uniq
        if missing:
            member_rows = self._db.fetch_all(
                "SELECT user_id, server_id FROM srv_members WHERE user_id IN ({})".format(
                    ",".join(["?"] * len(missing))
                ),
                tuple(missing),
            )
            for row in member_rows:
                uid = int(row["user_id"])
                sid = int(row["server_id"])
                if uid not in result:
                    result[uid] = set()
                result[uid].add(sid)
        if valkey_available():
            for uid in missing:
                cache_set(f"user:servers:{uid}", list(result.get(uid, set())), ttl=300)
        # Every requested user must be present in the map, even with no
        # memberships, so callers can index directly without .get()
        # fallbacks.
        for uid in uniq:
            result.setdefault(uid, set())
        return result

    def _get_user_friend_ids(self, user_id: int) -> set:
        rows = self._db.fetch_all(
            "SELECT friend_id FROM rel_friends WHERE user_id = ?",
            (user_id,),
        )
        return {row["friend_id"] for row in rows}

    def _get_user_friends_map(self, user_ids: List[int]) -> Dict[int, set]:
        from src.core.database import cache_get, cache_set, valkey_available

        uniq = sorted({int(uid) for uid in user_ids if uid})
        result: Dict[int, set] = {}
        missing: List[int] = []
        if valkey_available():
            for uid in uniq:
                cached = cache_get(f"user:friends:{uid}")
                if isinstance(cached, list):
                    result[uid] = {int(x) for x in cached}
                else:
                    missing.append(uid)
        else:
            missing = uniq
        if missing:
            friend_rows = []
            for uid in missing:
                rows = self._db.fetch_all(
                    "SELECT user_id, friend_id FROM rel_friends WHERE user_id = ?",
                    (uid,),
                )
                friend_rows.extend(rows)
            for row in friend_rows:
                uid = int(row["user_id"])
                fid = int(row["friend_id"])
                if uid not in result:
                    result[uid] = set()
                result[uid].add(fid)
        if valkey_available():
            for uid in missing:
                cache_set(f"user:friends:{uid}", list(result.get(uid, set())), ttl=300)
        # Every requested user must be present in the map, even with no
        # friends, so callers can index directly without .get()
        # fallbacks.
        for uid in uniq:
            result.setdefault(uid, set())
        return result
