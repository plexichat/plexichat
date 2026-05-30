from typing import List, Optional

from .base import SearchManagerBase
from ..exceptions import SearchLimitError
from ..models import IndexedUser, UserSearchResult, UserSearchResultPage


class UserSearchMixin(SearchManagerBase):
    def search_users(
        self,
        user_id: int,
        query: str,
        server_id: Optional[int] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[UserSearchResult]:
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit,
            )

        self._check_rate_limit(user_id)

        results = self._indexer.search_users(query, limit * 2, offset)

        if server_id:
            server_members = self._get_server_member_ids(server_id)
            results = [r for r in results if r.user_id in server_members]

        results = self._enrich_user_results(results, user_id)

        results = self._ranking_engine.rank_user_results(results, query, user_id)

        return results[:limit]

    def search_users_page(
        self,
        user_id: int,
        query: str,
        server_id: Optional[int] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> UserSearchResultPage:
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit,
            )

        self._check_rate_limit(user_id)

        results, next_cursor = self._indexer.search_users_page(query, limit, cursor)

        if server_id:
            server_members = self._get_server_member_ids(server_id)
            results = [r for r in results if r.user_id in server_members]

        results = self._enrich_user_results(results, user_id)
        results = self._ranking_engine.rank_user_results(results, query, user_id)

        return UserSearchResultPage(results=results, next_cursor=next_cursor)

    def index_user(
        self,
        user_id: int,
        username: str,
        display_name: Optional[str] = None,
        is_bot: bool = False,
    ) -> None:
        indexed = IndexedUser(
            user_id=user_id,
            username=username,
            display_name=display_name,
            is_bot=is_bot,
        )

        self._indexer.index_user(indexed)

        now = self._get_timestamp()
        self._db.upsert(
            "search_user_index",
            ["user_id", "indexed_at", "updated_at"],
            (user_id, now, now),
            ["user_id"],
            ["updated_at"],
        )
