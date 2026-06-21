from typing import Dict, List

from .base import SearchManagerBase
from ..models import MessageSearchResult, UserSearchResult


class EnrichmentMixin(SearchManagerBase):
    def _enrich_message_results(
        self,
        results: List[MessageSearchResult],
        user_id: int,
    ) -> List[MessageSearchResult]:
        author_ids = {r.author_id for r in results if r.author_id}
        conversation_ids = {r.conversation_id for r in results if r.conversation_id}
        server_ids = {r.server_id for r in results if r.server_id}
        channel_ids = {r.channel_id for r in results if r.channel_id}

        authors = self._get_names(
            author_ids, "user:username", "auth_users", "id", "username", ttl=60
        )
        conversations = self._get_names(
            conversation_ids,
            "conversation:name",
            "msg_conversations",
            "id",
            "name",
            ttl=300,
        )
        servers = self._get_names(
            server_ids, "server:name", "srv_servers", "id", "name", ttl=300
        )
        channels = self._get_names(
            channel_ids, "channel:name", "srv_channels", "id", "name", ttl=300
        )

        for result in results:
            if result.author_id and result.author_id in authors:
                result.author_username = authors[result.author_id]
            if result.conversation_id and result.conversation_id in conversations:
                result.conversation_name = conversations[result.conversation_id]
            if result.server_id and result.server_id in servers:
                result.server_name = servers[result.server_id]
            if result.channel_id and result.channel_id in channels:
                result.channel_name = channels[result.channel_id]

        return results

    def _enrich_user_results(
        self,
        results: List[UserSearchResult],
        user_id: int,
    ) -> List[UserSearchResult]:
        current_user_servers = self._get_user_server_ids(user_id)
        target_ids = [r.user_id for r in results]
        targets_map = self._get_user_servers_map(target_ids)
        for r in results:
            r.mutual_servers = len(
                current_user_servers & targets_map.get(r.user_id, set())
            )
        return results

    def _get_names(
        self,
        ids: set,
        cache_prefix: str,
        table: str,
        id_col: str,
        name_col: str,
        ttl: int = 300,
    ) -> Dict[int, str]:
        # N+1 FIX: previously, this method did one cache lookup + one
        # SQL fetch per id.  We collapse both into single bulk calls:
        # Redis MGET for cache, single ``WHERE id IN (...)`` for the
        # SQL fallback.  Same external contract: a dict ``{id: name}``
        # containing only ids we could resolve (callers use
        # ``.get(i)`` for safe misses).
        from src.core.database import (
            cache_set,
            cache_get_many,  # type: ignore[attr-defined]  # duck-typed on cache backend
            dialect,
            redis_available,
        )

        safe_table = dialect.sanitize_identifier(table, self._db.type)
        safe_id_col = dialect.sanitize_identifier(id_col, self._db.type)
        safe_name_col = dialect.sanitize_identifier(name_col, self._db.type)

        uniq = sorted({int(i) for i in ids if i})
        if not uniq:
            return {}
        cached: Dict[int, str] = {}
        missing: List[int] = []

        if redis_available():
            # Bulk cache read: single MGET round-trip instead of N
            # individual cache_get calls.
            keys = [f"{cache_prefix}:{i}" for i in uniq]
            bulk_values = cache_get_many(keys) or {}
            for i, key in zip(uniq, keys):
                v = bulk_values.get(key)
                if isinstance(v, str):
                    cached[i] = v
                else:
                    missing.append(i)
        else:
            missing = uniq

        if missing:
            # Single SQL fetch for all missing ids, chunked to stay
            # under the Python ``sqlite3`` default 999-bind-variable
            # cap; Postgres allows 32k but chunking costs nothing.
            chunk_size = 500
            for start in range(0, len(missing), chunk_size):
                chunk = missing[start : start + chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                rows = self._db.fetch_all(
                    f"SELECT {safe_id_col} as id, {safe_name_col} as name "
                    f"FROM {safe_table} WHERE {safe_id_col} IN ({placeholders})",
                    tuple(chunk),
                )
                for row in rows:
                    i_val = int(row["id"])
                    n_val = row["name"]
                    cached[i_val] = n_val
                    if redis_available():
                        cache_set(f"{cache_prefix}:{i_val}", n_val, ttl=ttl)
        return cached
