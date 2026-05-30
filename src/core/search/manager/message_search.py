from typing import List, Optional

from .base import SearchManagerBase
from ..exceptions import SearchLimitError
from ..models import MessageSearchResult, MessageSearchResultPage


class MessageSearchMixin(SearchManagerBase):
    def search_messages(
        self,
        user_id: int,
        query: str,
        conversation_id: Optional[int] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        author_id: Optional[int] = None,
        after_timestamp: Optional[int] = None,
        has_attachments: Optional[bool] = None,
        mentions_user: Optional[int] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[MessageSearchResult]:
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit,
            )

        self._check_rate_limit(user_id)

        parsed = self._query_parser.parse(query)

        accessible_conversations = self._get_accessible_conversations(
            user_id, conversation_id, server_id, channel_id
        )

        if not accessible_conversations:
            return []

        search_text = parsed.search_text if parsed.search_terms else query

        results = self._indexer.search_messages(
            query=search_text,
            conversation_ids=accessible_conversations,
            server_ids=[server_id] if server_id else None,
            channel_ids=[channel_id] if channel_id else None,
            author_ids=[author_id] if author_id is not None else None,
            limit=limit * 2,
            offset=offset,
        )

        if after_timestamp is not None:
            results = [
                r for r in results if r.created_at and r.created_at > after_timestamp
            ]

        if has_attachments is not None:
            results = [r for r in results if r.has_attachments == has_attachments]

        if mentions_user is not None:
            results = [
                r
                for r in results
                if f"<@{mentions_user}>" in (r.content or "")
                or f"<@!{mentions_user}>" in (r.content or "")
            ]

        results = self._filter_processor.apply_filters(results, parsed, user_id)

        results = self._enrich_message_results(results, user_id)

        results = self._ranking_engine.rank_message_results(
            results, parsed, self._get_timestamp()
        )

        return results[:limit]

    def search_messages_page(
        self,
        user_id: int,
        query: str,
        conversation_id: Optional[int] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        author_id: Optional[int] = None,
        after_timestamp: Optional[int] = None,
        has_attachments: Optional[bool] = None,
        mentions_user: Optional[int] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> MessageSearchResultPage:
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit,
            )

        self._check_rate_limit(user_id)

        parsed = self._query_parser.parse(query)
        accessible_conversations = self._get_accessible_conversations(
            user_id, conversation_id, server_id, channel_id
        )

        if not accessible_conversations:
            return MessageSearchResultPage(results=[], next_cursor=None)

        search_text = parsed.search_text if parsed.search_terms else query

        results, next_cursor = self._indexer.search_messages_page(
            query=search_text,
            conversation_ids=accessible_conversations,
            server_ids=[server_id] if server_id else None,
            channel_ids=[channel_id] if channel_id else None,
            author_ids=[author_id] if author_id is not None else None,
            limit=limit,
            cursor=cursor,
        )

        if after_timestamp is not None:
            results = [
                r for r in results if r.created_at and r.created_at > after_timestamp
            ]

        if has_attachments is not None:
            results = [r for r in results if r.has_attachments == has_attachments]

        if mentions_user is not None:
            results = [
                r
                for r in results
                if f"<@{mentions_user}>" in (r.content or "")
                or f"<@!{mentions_user}>" in (r.content or "")
            ]

        results = self._filter_processor.apply_filters(results, parsed, user_id)
        results = self._enrich_message_results(results, user_id)
        results = self._ranking_engine.rank_message_results(
            results, parsed, self._get_timestamp()
        )

        return MessageSearchResultPage(results=results, next_cursor=next_cursor)

    def search_server_messages(
        self,
        user_id: int,
        server_id: int,
        query: str,
        limit: int = 25,
        offset: int = 0,
    ) -> List[MessageSearchResult]:
        if not self._can_access_server(user_id, server_id):
            return []
        return self.search_messages(
            user_id, query, server_id=server_id, limit=limit, offset=offset
        )
