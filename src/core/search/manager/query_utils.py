from typing import List

from .base import SearchManagerBase
from ..models import ParsedQuery

# Core-boundary limits. The HTTP route also clamps these values, but the
# manager is directly callable from internal/test callers, so it must stay
# safe on its own (the same defence the FTS indexer applies via
# ``_bounded_page``). Without the lower clamp a negative limit reaches
# SQLite's ``LIMIT ?`` verbatim, where -1 means *unlimited* and would turn a
# single autocomplete request into a full search_history dump. An unbounded
# upper limit / needle has the same effect with a large enough value.
_MAX_SUGGESTION_QUERY_CHARS = 128
_MAX_SUGGESTIONS = 100


def _escape_like(text: str) -> str:
    """Escape LIKE metacharacters so user text is matched literally.

    Without this a ``%`` or ``_`` in the *prefix* half of a suggestion query
    is treated as a wildcard, so e.g. a bare ``%`` matches the caller's whole
    search-history instead of the (non-existent) queries starting with it.
    The escape character (``\\``) is declared in the query via ``ESCAPE '\\'``.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class QueryUtilsMixin(SearchManagerBase):
    def parse_query(self, query: str) -> ParsedQuery:
        return self._query_parser.parse(query)

    def get_search_suggestions(
        self,
        user_id: int,
        partial_query: str,
        limit: int = 10,
    ) -> List[str]:
        suggestions = []

        # Clamp before any SQL is built. ``int()`` guards against non-numeric
        # callers; bool/None/nan never reach the LIMIT parameter.
        try:
            limit = int(limit)
        except (TypeError, ValueError, OverflowError):
            limit = 10
        limit = max(0, min(limit, _MAX_SUGGESTIONS))
        if limit == 0:
            return []

        partial_query = str(partial_query or "")[:_MAX_SUGGESTION_QUERY_CHARS]

        filter_suggestions = self._query_parser.get_filter_suggestions(partial_query)
        suggestions.extend(filter_suggestions)

        if len(suggestions) < limit:
            # GROUP BY rewrite: SELECT DISTINCT + ORDER BY on a non-selected
            # column (searched_at) is invalid on Postgres.  Aggregating the
            # ordering column keeps identical semantics on SQLite and PG.
            #
            # The caller's prefix is escaped so ``%``/``_``/``\`` are matched
            # literally rather than as wildcards (docs/admin-hardening.md:
            # "Treat %, _, and \ literally in search queries").
            prefix = _escape_like(str(partial_query))
            history = self._db.fetch_all(
                """SELECT query FROM search_history
                   WHERE user_id = ? AND query LIKE ? ESCAPE '\\'
                   GROUP BY query
                   ORDER BY MAX(searched_at) DESC LIMIT ?""",
                (user_id, f"{prefix}%", limit - len(suggestions)),
            )
            suggestions.extend(row["query"] for row in history)

        return suggestions[:limit]
