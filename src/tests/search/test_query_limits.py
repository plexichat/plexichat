"""Regression tests: search query limits and empty-query consistency.

F6 (audit): pathological queries — multi-KB strings or thousands of terms —
were parsed and executed against the backend MATCH/ILIKE expression with no
bound; the result-limit never had a chance to apply. Every search entry
point now validates the query up front.

F16 (audit): empty/whitespace queries were handled inconsistently — message
search returned an empty result set while user search fell through to the
supplemental directory fallback, whose ``LIKE '%q%'`` degenerated to a
whole-directory scan for an empty needle. All entry points now raise the
same 400-class InvalidQuerySyntaxError.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

from src.core.search.exceptions import InvalidQuerySyntaxError, SearchQueryError
from src.core.search.manager.message_search import (
    MAX_QUERY_LENGTH,
    MAX_QUERY_TERMS,
    validate_search_query,
)


class TestValidateSearchQuery:
    def test_rejects_empty_query(self):
        with pytest.raises(SearchQueryError):
            validate_search_query("")

    def test_rejects_whitespace_query(self):
        with pytest.raises(SearchQueryError):
            validate_search_query("   \t\n  ")

    def test_rejects_non_string_query(self):
        with pytest.raises(SearchQueryError):
            validate_search_query(12345)

    def test_rejects_oversized_query(self):
        with pytest.raises(SearchQueryError):
            validate_search_query("a" * (MAX_QUERY_LENGTH + 1))

    def test_rejects_too_many_terms(self):
        query = " ".join(f"term{i}" for i in range(MAX_QUERY_TERMS + 1))
        with pytest.raises(SearchQueryError):
            validate_search_query(query)

    def test_accepts_normal_query(self):
        validate_search_query("hello world")
        validate_search_query("x" * MAX_QUERY_LENGTH)


class TestSearchEndpointsConsistent:
    """Every search entry point answers a bad query the same way (F16)."""

    def test_message_search_rejects_empty(self, search_manager, two_users):
        user1, _user2 = two_users
        with pytest.raises(SearchQueryError):
            search_manager.search_messages(user_id=user1.id, query="   ")

    def test_message_search_rejects_oversized(self, search_manager, two_users):
        user1, _user2 = two_users
        with pytest.raises(SearchQueryError):
            search_manager.search_messages(user_id=user1.id, query="a" * 5000)

    def test_message_search_page_rejects_empty(self, search_manager, two_users):
        user1, _user2 = two_users
        with pytest.raises(SearchQueryError):
            search_manager.search_messages_page(user_id=user1.id, query="")

    def test_user_search_rejects_empty(self, search_manager, two_users):
        user1, _user2 = two_users
        with pytest.raises(SearchQueryError):
            search_manager.search_users(user_id=user1.id, query="")

    def test_user_search_page_rejects_empty(self, search_manager, two_users):
        user1, _user2 = two_users
        with pytest.raises(SearchQueryError):
            search_manager.search_users_page(user_id=user1.id, query="")

    def test_user_search_empty_no_directory_scan(self, search_manager, two_users):
        """The empty query must fail BEFORE the directory fallback runs."""
        user1, _user2 = two_users
        with pytest.raises(SearchQueryError):
            search_manager.search_users(user_id=user1.id, query=" ")
