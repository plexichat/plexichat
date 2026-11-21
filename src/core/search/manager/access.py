from typing import Dict, Iterable, List, Optional

from src.core.servers.permission_utils import has_permission as _check_permission

from .base import SearchManagerBase

# Chunk ``IN (...)`` binds so SQLite's default 999-variable cap is never
# approached; Postgres accepts far more but chunking is harmless there.
_BIND_CHUNK = 500

# ``RelationshipManager.get_friend_ids`` is ``@cached(prefix="user_friends")``
# and every send/accept/remove/block path invalidates that prefix for the
# users involved. Read AND write the very same key here instead of a private
# ``user:friends:`` namespace: a parallel namespace has no invalidator, so a
# removed/blocked friend kept counting as mutual for the full cache TTL.
# The two caches also share the same value shape (a list of friend ids).
_USER_FRIENDS_CACHE_PREFIX = "user_friends"


def _user_friends_cache_key(user_id: int) -> str:
    return f"{_USER_FRIENDS_CACHE_PREFIX}:int:{int(user_id)}"


class AccessControlMixin(SearchManagerBase):
    @staticmethod
    def _as_id(value) -> Optional[int]:
        """Coerce an indexed id to a positive int, or ``None`` if malformed."""
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if parsed > 0 else None

    @classmethod
    def _chunks(cls, values: Iterable[int]):
        uniq = sorted({parsed for v in values if (parsed := cls._as_id(v)) is not None})
        for start in range(0, len(uniq), _BIND_CHUNK):
            yield uniq[start : start + _BIND_CHUNK]

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

    def _can_access_conversations_bulk(
        self, user_id: int, conversation_ids: Iterable[int]
    ) -> Dict[int, bool]:
        """Resolve DM/group conversation access for many ids in bounded queries.

        One ``msg_participants`` lookup per chunk replaces the old per-result
        ``SELECT 1`` loop. The answer is keyed by every requested id, with
        ``False`` as the default so an unresolved id fails closed.
        """
        result: Dict[int, bool] = {}
        for chunk in self._chunks(conversation_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = self._db.fetch_all(
                "SELECT conversation_id FROM msg_participants "
                f"WHERE user_id = ? AND conversation_id IN ({placeholders})",
                (user_id, *chunk),
            )
            # SECURITY: a live msg_participants.conversation_id vouches for the
            # searcher's visibility only while it is a genuine integer id.
            # Coercing a malformed-but-coercible value with int() ("05"->5,
            # 5.9->5, True->1) would admit a conversation the row does not
            # prove the user participates in, leaking its messages in search.
            # Fail closed: only exact integer ids from the authoritative
            # projection may enter the allow set.
            allowed = {
                row["conversation_id"]
                for row in rows
                if type(row["conversation_id"]) is int
            }
            for cid in chunk:
                result[cid] = cid in allowed
        return result

    def _can_access_channels_bulk(
        self, user_id: int, channel_ids: Iterable[int]
    ) -> Dict[int, bool]:
        """Resolve per-channel ``messages.read`` for many channels at once.

        Previously every result (and every channel of a server-wide search)
        issued its own ``srv_channels`` read plus a permission resolution.
        Here the channel->server mapping is fetched in one bounded query and
        permissions are computed in batch through the role handler's set-based
        API, with a per-channel fallback for servers/modules that lack it.
        The contract is unchanged: a channel that is missing, has no server,
        or cannot be proven readable resolves to ``False`` (fail closed).
        """
        result: Dict[int, bool] = {
            parsed: False
            for cid in channel_ids
            if (parsed := self._as_id(cid)) is not None
        }
        if not result or not self._servers:
            return result

        server_for: Dict[int, int] = {}
        for chunk in self._chunks(result):
            placeholders = ",".join("?" for _ in chunk)
            rows = self._db.fetch_all(
                f"SELECT id, server_id FROM srv_channels WHERE id IN ({placeholders})",
                tuple(chunk),
            )
            for row in rows:
                server_id = row["server_id"]
                if server_id is not None:
                    server_for[int(row["id"])] = int(server_id)

        by_server: Dict[int, List[int]] = {}
        for cid, server_id in server_for.items():
            by_server.setdefault(server_id, []).append(cid)

        role_handler = getattr(self._servers, "role_handler", None)
        batch_api = getattr(role_handler, "get_permissions_batch", None)
        for server_id, cids in by_server.items():
            perms_map = None
            if callable(batch_api):
                try:
                    batch_result = batch_api(user_id, server_id, cids)
                    # Unknown/corrupt permission data must not turn into a
                    # broader grant; fall back to the canonical single check.
                    perms_map = batch_result if isinstance(batch_result, dict) else None
                except Exception:
                    perms_map = None
            for cid in cids:
                if perms_map is not None:
                    allowed = _check_permission(perms_map.get(cid, {}), "messages.read")
                else:
                    allowed = self._servers.has_permission(
                        user_id, server_id, "messages.read", cid
                    )
                result[cid] = bool(allowed)
        return result

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
                "SELECT id AS channel_id, conversation_id FROM srv_channels "
                "WHERE server_id = ? AND conversation_id IS NOT NULL",
                (server_id,),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT c.id AS channel_id, c.conversation_id
                   FROM srv_channels c
                   JOIN srv_members m ON c.server_id = m.server_id
                   WHERE m.user_id = ? AND c.conversation_id IS NOT NULL""",
                (user_id,),
            )

        # Membership of a server alone does not grant read access to every
        # channel (private / role-gated / overridden channels). Resolve the
        # whole candidate set in bulk rather than issuing one permission query
        # per channel; the per-channel result still fails closed.
        channel_to_conv = {
            int(row["channel_id"]): row["conversation_id"] for row in rows
        }
        if channel_to_conv:
            readable = self._can_access_channels_bulk(user_id, channel_to_conv)
            conversations.extend(
                channel_to_conv[cid] for cid, allowed in readable.items() if allowed
            )

        return list(set(conversations))

    def _verify_results_visible(
        self,
        user_id: int,
        results: List,
        access_cache: Optional[Dict] = None,
    ) -> List:
        """Re-verify per-result visibility before returning search results.

        Defense-in-depth for global searches with explicit filters
        (``from:`` / ``in:``): every returned message must resolve to a
        conversation/channel the searcher can actually see.  Results that
        fail the check are dropped rather than leaked.

        ``access_cache`` is an optional *request-scoped* memo (see
        ``search_messages_page``). A single paged search verifies each backend
        page, and the same channels/conversations recur across pages; the memo
        lets those ids be resolved once for the whole request instead of once
        per page. It is keyed to ``user_id`` and never shared across requests,
        so it cannot leak one user's ACL decisions into another's search and
        cannot outlive a permission change.
        """
        # Collect the candidate ids first so access can be resolved with a
        # bounded number of set-based queries instead of two queries per hit.
        channel_ids = set()
        conversation_ids = set()
        for r in results:
            channel_id = self._as_id(getattr(r, "channel_id", None))
            conversation_id = self._as_id(getattr(r, "conversation_id", None))
            # A row with a malformed channel id must not fall through to a
            # conversation-id check and become visible by accident: keep the
            # original "channel id present" intent and let it fail closed.
            if getattr(r, "channel_id", None) is not None:
                if channel_id is not None:
                    channel_ids.add(channel_id)
            elif conversation_id is not None:
                conversation_ids.add(conversation_id)

        if access_cache is None:
            channel_ok = self._can_access_channels_bulk(user_id, channel_ids)
            conversation_ok = self._can_access_conversations_bulk(
                user_id, conversation_ids
            )
        else:
            # Never reuse a memo that belongs to a different user. Guarding on
            # user_id keeps the cached decisions authz-scoped even if a caller
            # accidentally passes a scratch dict from another search.
            if access_cache.get("user_id") != user_id:
                access_cache.clear()
                access_cache["user_id"] = user_id
            channel_ok = dict(access_cache.get("channels", {}))
            conversation_ok = dict(access_cache.get("conversations", {}))
            pending_channels = channel_ids.difference(channel_ok)
            pending_conversations = conversation_ids.difference(conversation_ok)
            if pending_channels:
                channel_ok.update(
                    self._can_access_channels_bulk(user_id, pending_channels)
                )
            if pending_conversations:
                conversation_ok.update(
                    self._can_access_conversations_bulk(user_id, pending_conversations)
                )
            access_cache["channels"] = channel_ok
            access_cache["conversations"] = conversation_ok

        verified = []
        for r in results:
            raw_channel = getattr(r, "channel_id", None)
            channel_id = self._as_id(raw_channel)
            conversation_id = self._as_id(getattr(r, "conversation_id", None))
            if raw_channel is not None:
                if channel_id is not None and channel_ok.get(channel_id, False):
                    verified.append(r)
            elif conversation_id is not None:
                if conversation_ok.get(conversation_id, False):
                    verified.append(r)
            # Rows with neither a channel nor a conversation id cannot be
            # proven visible to the searcher, so they are dropped
            # (fail-closed) rather than leaked.
        return verified

    def _get_server_member_ids(self, server_id: int) -> set:
        """Resolve a server's member ids as the user-search visibility scope.

        Server-scoped user search (including typeahead) resolves this set on
        *every* keystroke: it is the result-visibility scope folded into the
        short-TTL result cache key, which is intentionally computed before the
        cache lookup. That used to issue a fresh ``SELECT user_id FROM
        srv_members WHERE server_id = ?`` full projection per search, so a
        burst over a large server replayed the whole roster read each time.

        Reuse the bounded, short-TTL, write-invalidated member-id cache that
        gateway/presence/voice fan-out already uses for this exact query. It is
        keyed only by ``(server_id, exclude_user_id)`` -- never by the searching
        credential -- and holds the identical authoritative projection, so it
        cannot carry one user's authorization into another's search. Every
        join/kick/ban/leave clears it through the shared membership funnel, so
        result scoping stays in lockstep with roster writes.
        """
        from src.core.servers.manager.member_id_cache import (
            _MISS as _MEMBER_IDS_MISS,
            get_cached_member_user_ids,
            prime_member_user_ids,
        )

        cached = get_cached_member_user_ids(server_id)
        if cached is not _MEMBER_IDS_MISS:
            return set(cached)

        rows = self._db.fetch_all(
            "SELECT user_id FROM srv_members WHERE server_id = ?", (server_id,)
        )
        user_ids = [row["user_id"] for row in rows]
        prime_member_user_ids(server_id, None, user_ids)
        return set(user_ids)

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
        # N+1 FIX: hydrate every hit user's server set with one bulk cache
        # read (MGET) and chunked ``WHERE user_id IN (...)`` SQL queries,
        # instead of one cache GET + one unbounded SQL SELECT per result. This
        # mirrors the batching already done by ``_get_user_friends_map``. The
        # external contract is unchanged: a map keyed by exactly the requested
        # ids, each value that user's server ids (empty set on misses).
        from src.core.database import cache_get_many, cache_set, valkey_available

        uniq = sorted({int(uid) for uid in user_ids if uid})
        result: Dict[int, set] = {}
        missing: List[int] = []
        if valkey_available():
            # One MGET instead of one Valkey round-trip per hydrated hit. Use
            # the authoritative key that cache_ops invalidates on membership
            # changes so a removed server cannot keep counting for the TTL.
            keys = [f"user:servers:{uid}" for uid in uniq]
            bulk = cache_get_many(keys) or {}
            for uid, key in zip(uniq, keys):
                cached = bulk.get(key)
                if isinstance(cached, list):
                    result[uid] = {int(x) for x in cached}
                else:
                    missing.append(uid)
        else:
            missing = uniq
        if missing:
            # Bounded IN (...) batches replace the old single unbounded bind
            # list (which could exceed SQLite's variable cap) and keep SQL
            # cost at ceil(N / _BIND_CHUNK) queries rather than N.
            for start in range(0, len(missing), _BIND_CHUNK):
                chunk = missing[start : start + _BIND_CHUNK]
                placeholders = ",".join("?" for _ in chunk)
                member_rows = self._db.fetch_all(
                    "SELECT user_id, server_id FROM srv_members "
                    f"WHERE user_id IN ({placeholders})",
                    tuple(chunk),
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
        # N+1 FIX: hydrate every hit user's friend set with one bulk cache
        # read (MGET) and chunked ``WHERE user_id IN (...)`` SQL queries,
        # instead of one cache GET + one SQL SELECT per result.  This mirrors
        # the batching already done by ``_get_user_servers_map``.  The external
        # contract is unchanged: a map keyed by exactly the requested ids,
        # each value the direct friend ids of that user (empty set on misses).
        from src.core.database import (
            cache_get_many,
            cache_set,
            valkey_available,
        )

        uniq = sorted({int(uid) for uid in user_ids if uid})
        result: Dict[int, set] = {}
        missing: List[int] = []
        if valkey_available():
            # Single MGET instead of one round-trip per hydrated hit. Use the
            # authoritative friend-set key so relationship mutations evict it.
            keys = [_user_friends_cache_key(uid) for uid in uniq]
            bulk = cache_get_many(keys) or {}
            for uid, key in zip(uniq, keys):
                cached = bulk.get(key)
                if isinstance(cached, list):
                    result[uid] = {int(x) for x in cached}
                else:
                    missing.append(uid)
        else:
            missing = uniq

        if missing:
            # Bounded IN (...) batches replace the per-user SELECT loop.
            # Chunk by the raw missing list (not the id-coercing helper) so
            # every requested key keeps the exact scope it had before.
            for start in range(0, len(missing), _BIND_CHUNK):
                chunk = missing[start : start + _BIND_CHUNK]
                placeholders = ",".join("?" for _ in chunk)
                friend_rows = self._db.fetch_all(
                    "SELECT user_id, friend_id FROM rel_friends "
                    f"WHERE user_id IN ({placeholders})",
                    tuple(chunk),
                )
                for row in friend_rows:
                    uid = int(row["user_id"])
                    fid = int(row["friend_id"])
                    if uid not in result:
                        result[uid] = set()
                    result[uid].add(fid)
        if valkey_available():
            for uid in missing:
                cache_set(
                    _user_friends_cache_key(uid),
                    list(result.get(uid, set())),
                    ttl=300,
                )
        # Every requested user must be present in the map, even with no
        # friends, so callers can index directly without .get()
        # fallbacks.
        for uid in uniq:
            result.setdefault(uid, set())
        return result
