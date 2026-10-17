from typing import List

from .base import SearchManagerBase
from ..models import ParsedQuery


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

        filter_suggestions = self._query_parser.get_filter_suggestions(partial_query)
        suggestions.extend(filter_suggestions)

        if len(suggestions) < limit:
            # GROUP BY rewrite: SELECT DISTINCT + ORDER BY on a non-selected
            # column (searched_at) is invalid on Postgres.  Aggregating the
            # ordering column keeps identical semantics on SQLite and PG.
            history = self._db.fetch_all(
                """SELECT query FROM search_history
                   WHERE user_id = ? AND query LIKE ?
                   GROUP BY query
                   ORDER BY MAX(searched_at) DESC LIMIT ?""",
                (user_id, f"{partial_query}%", limit - len(suggestions)),
            )
            suggestions.extend(row["query"] for row in history)

        return suggestions[:limit]
