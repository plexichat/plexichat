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
        from src.core.database import cache_get, cache_set, dialect, redis_available

        safe_table = dialect.sanitize_identifier(table, self._db.type)
        safe_id_col = dialect.sanitize_identifier(id_col, self._db.type)
        safe_name_col = dialect.sanitize_identifier(name_col, self._db.type)

        uniq = sorted({int(i) for i in ids if i})
        if not uniq:
            return {}
        cached: Dict[int, str] = {}
        missing: List[int] = []
        if redis_available():
            for i in uniq:
                v = cache_get(f"{cache_prefix}:{i}")
                if isinstance(v, str):
                    cached[i] = v
                else:
                    missing.append(i)
        else:
            missing = uniq
        if missing:
            for i in missing:
                row = self._db.fetch_one(
                    f"SELECT {safe_id_col} as id, {safe_name_col} as name FROM {safe_table} WHERE {safe_id_col} = ?",
                    (i,),
                )
                if row:
                    i_val = int(row["id"])
                    n_val = row["name"]
                    cached[i_val] = n_val
                    if redis_available():
                        cache_set(f"{cache_prefix}:{i_val}", n_val, ttl=ttl)
        return cached
