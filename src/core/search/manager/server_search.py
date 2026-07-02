from typing import List, Optional

from .base import SearchManagerBase
from ..exceptions import SearchLimitError
from ..models import IndexedServer, ServerSearchResult, ServerSearchResultPage


class ServerSearchMixin(SearchManagerBase):
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

        self._check_rate_limit(user_id)

        results = self._indexer.search_servers(
            query=query,
            category=category,
            public_only=True,
            limit=limit * 2,
            offset=offset,
        )

        results = self._enrich_server_results(results)

        results = self._ranking_engine.rank_server_results(results, query)

        return results[:limit]

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

        self._check_rate_limit(user_id)

        results, next_cursor = self._indexer.search_servers_page(
            query=query,
            category=category,
            public_only=True,
            limit=limit,
            cursor=cursor,
        )

        results = self._enrich_server_results(results)
        results = self._ranking_engine.rank_server_results(results, query)

        return ServerSearchResultPage(results=results, next_cursor=next_cursor)

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
