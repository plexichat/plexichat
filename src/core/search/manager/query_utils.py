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
            history = self._db.fetch_all(
                """SELECT DISTINCT query FROM search_history 
                   WHERE user_id = ? AND query LIKE ?
                   ORDER BY searched_at DESC LIMIT ?""",
                (user_id, f"{partial_query}%", limit - len(suggestions)),
            )
            suggestions.extend(row["query"] for row in history)

        return suggestions[:limit]
