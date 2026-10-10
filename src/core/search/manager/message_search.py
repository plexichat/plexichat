from typing import List, Optional

from .base import SearchManagerBase
from ..exceptions import InvalidQuerySyntaxError, SearchLimitError
from ..models import MessageSearchResult, MessageSearchResultPage

# F6: bound pathological queries BEFORE parsing/execution. A multi-megabyte
# query or a query with thousands of terms burns CPU in the parser and in
# the backend MATCH/ILIKE expression on every request — the result-limit
# never gets a chance to apply.
MAX_QUERY_LENGTH = 256
MAX_QUERY_TERMS = 16


def validate_search_query(query: str, *, has_filters: bool = False) -> None:
    """Reject malformed, oversized, or empty search queries.

    Shared by the message and user search paths so every endpoint answers
    the same 400-class error (F16: previously messages and users disagreed
    on empty-query behavior — one returned empty results, the other fell
    through to a whole-directory LIKE scan).

    An empty text query is still valid when structured filters carry the
    request (mentions_user / has_attachments): filter-only search is a
    real capability, so ``has_filters`` opts out of the empty check only.
    """
    if not isinstance(query, str):
        raise InvalidQuerySyntaxError("Query must be a string")
    if not query.strip():
        if has_filters:
            return
        raise InvalidQuerySyntaxError("Query must not be empty")
    if len(query) > MAX_QUERY_LENGTH:
        raise InvalidQuerySyntaxError(
            f"Query exceeds maximum length of {MAX_QUERY_LENGTH} characters"
        )
    if len(query.split()) > MAX_QUERY_TERMS:
        raise InvalidQuerySyntaxError(
            f"Query exceeds maximum of {MAX_QUERY_TERMS} terms"
        )


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

        validate_search_query(
            query,
            has_filters=(mentions_user is not None or has_attachments is not None),
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

        # Re-verify per-result channel/conversation visibility server-side:
        # explicit filters (from:/in:) search globally and must never return
        # messages from private channels the searcher cannot access.
        results = self._verify_results_visible(user_id, results)

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

        validate_search_query(
            query,
            has_filters=(mentions_user is not None or has_attachments is not None),
        )

        self._check_rate_limit(user_id)

        parsed = self._query_parser.parse(query)
        accessible_conversations = self._get_accessible_conversations(
            user_id, conversation_id, server_id, channel_id
        )

        if not accessible_conversations:
            return MessageSearchResultPage(results=[], next_cursor=None)

        search_text = parsed.search_text if parsed.search_terms else query

        # Fetch backend pages of max(limit*2, 50) rows per round-trip.
        # Post-index filters (mentions, attachments, and query filters) can
        # remove rows, so we keep fetching pages until the requested limit
        # is filled or the backend is exhausted; the backend cursor always
        # points past the last fully-examined page so the client never sees
        # duplicates. A hard cap (~500 scanned rows) bounds the work a
        # single page request can trigger; hitting it returns the page with
        # next_cursor=None rather than a cursor that skips unseen rows.
        _MAX_SCANNED_ROWS = 500
        backend_page_size = max(limit * 2, 50)

        collected = []
        page_cursor = cursor
        next_cursor = None
        scanned = 0
        while len(collected) < limit:
            if scanned >= _MAX_SCANNED_ROWS:
                # Scan budget exhausted before filling this page: stop
                # paginating entirely instead of handing back a cursor that
                # would skip the rows we never examined.
                next_cursor = None
                break
            page_results, page_next_cursor = self._indexer.search_messages_page(
                query=search_text,
                conversation_ids=accessible_conversations,
                server_ids=[server_id] if server_id else None,
                channel_ids=[channel_id] if channel_id else None,
                author_ids=[author_id] if author_id is not None else None,
                limit=backend_page_size,
                cursor=page_cursor,
            )
            if not page_results:
                next_cursor = None
                break

            scanned += len(page_results)

            filtered = page_results
            if after_timestamp is not None:
                filtered = [
                    r
                    for r in filtered
                    if r.created_at and r.created_at > after_timestamp
                ]
            if has_attachments is not None:
                filtered = [r for r in filtered if r.has_attachments == has_attachments]
            if mentions_user is not None:
                filtered = [
                    r
                    for r in filtered
                    if f"<@{mentions_user}>" in (r.content or "")
                    or f"<@!{mentions_user}>" in (r.content or "")
                ]

            filtered = self._filter_processor.apply_filters(filtered, parsed, user_id)
            # Per-result visibility re-check (see search_messages).
            filtered = self._verify_results_visible(user_id, filtered)
            filtered = self._enrich_message_results(filtered, user_id)
            filtered = self._ranking_engine.rank_message_results(
                filtered, parsed, self._get_timestamp()
            )
            collected.extend(filtered)

            next_cursor = page_next_cursor
            if not page_next_cursor or page_next_cursor == page_cursor:
                break
            page_cursor = page_next_cursor

        return MessageSearchResultPage(
            results=collected[:limit], next_cursor=next_cursor
        )

    def search_server_messages(
        self,
        user_id: int,
        server_id: int,
        query: str,
        channel_id: Optional[int] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[MessageSearchResult]:
        if not self._can_access_server(user_id, server_id):
            return []
        if channel_id is not None and not self._can_access_channel(user_id, channel_id):
            return []
        return self.search_messages(
            user_id,
            query,
            server_id=server_id,
            channel_id=channel_id,
            limit=limit,
            offset=offset,
        )
