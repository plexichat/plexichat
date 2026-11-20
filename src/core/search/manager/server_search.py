from typing import List, Optional

from .base import SearchManagerBase
from .message_search import validate_search_query
from .result_cache import CACHE_MISS, clear_result_caches, get_result_cache, make_key
from ..exceptions import SearchLimitError
from ..models import IndexedServer, ServerSearchResult, ServerSearchResultPage


class ServerSearchMixin(SearchManagerBase):
    def clear_cache(self) -> None:
        super().clear_cache()
        clear_result_caches(self)

    def search_servers(
        self,
        user_id: int,
        query: str,
        category: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[ServerSearchResult]:
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit,
            )

        # Bound the query before it reaches the indexer. Server search was
        # the one search entry point still missing this guard, so an internal
        # caller (or a route without a max_length constraint) could hand an
        # unbounded / empty string straight to websearch_to_tsquery / LIKE —
        # a CPU-exhaustion vector and an inconsistent error contract.
        validate_search_query(query)

        self._check_rate_limit(user_id)

        # Discovery searches are public_only, but the subject is still part of
        # the authz scope: an entry memoized for one caller is never served to
        # another, which keeps the no-cross-user-leak guarantee unconditional.
        cache = get_result_cache(self, "servers")
        cache_key = make_key(
            "servers.v1",
            (user_id,),
            query=query,
            category=category,
            limit=limit,
            offset=offset,
        )
        cached = cache.get(cache_key)
        if cached is not CACHE_MISS:
            return cached

        results = self._indexer.search_servers(
            query=query,
            category=category,
            public_only=True,
            limit=limit * 2,
            offset=offset,
        )

        results = self._enrich_server_results(results)

        results = self._ranking_engine.rank_server_results(results, query)

        final = results[:limit]
        cache.put(cache_key, final)
        return final

    def search_servers_page(
        self,
        user_id: int,
        query: str,
        category: Optional[str] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> ServerSearchResultPage:
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit,
            )

        # Mirror the non-paged path: never pass an unchecked query to the
        # backend (F6/F16 hardening applies to every search endpoint).
        validate_search_query(query)

        self._check_rate_limit(user_id)

        cache = get_result_cache(self, "servers_page")
        cache_key = make_key(
            "servers_page.v1",
            (user_id,),
            query=query,
            category=category,
            limit=limit,
            cursor=cursor,
        )
        cached = cache.get(cache_key)
        if cached is not CACHE_MISS:
            return cached

        results, next_cursor = self._indexer.search_servers_page(
            query=query,
            category=category,
            public_only=True,
            limit=limit,
            cursor=cursor,
        )

        results = self._enrich_server_results(results)
        results = self._ranking_engine.rank_server_results(results, query)

        final_page = ServerSearchResultPage(results=results, next_cursor=next_cursor)
        cache.put(cache_key, final_page)
        return final_page

    def index_server(
        self,
        server_id: int,
        name: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        category: Optional[str] = None,
        member_count: int = 0,
        is_public: bool = False,
    ) -> None:
        indexed = IndexedServer(
            server_id=server_id,
            name=name,
            description=description,
            tags=tags or [],
            category=category,
            member_count=member_count,
            is_public=is_public,
        )

        self._indexer.index_server(indexed)

        now = self._get_timestamp()
        self._db.upsert(
            "search_server_index",
            ["server_id", "indexed_at", "updated_at"],
            (server_id, now, now),
            ["server_id"],
            ["updated_at"],
        )
