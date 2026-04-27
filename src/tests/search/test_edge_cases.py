"""
Tests for edge cases and error handling.
"""

import pytest
import uuid

from src.core.search.exceptions import (
    SearchLimitError,
)
from src.core.search.models import ParsedQuery


@pytest.mark.search
class TestQueryEdgeCases:
    """Test query parsing edge cases."""

    def test_query_with_special_characters(self, db_and_search):
        """Test query with special characters."""
        db, auth, messaging, servers, search = db_and_search

        parsed = search.parse_query("hello @#$%^&*() world")

        assert isinstance(parsed, ParsedQuery)

    def test_query_with_unicode(self, db_and_search):
        """Test query with unicode characters."""
        db, auth, messaging, servers, search = db_and_search

        parsed = search.parse_query("hello world")

        assert isinstance(parsed, ParsedQuery)

    def test_query_with_only_filters(self, db_and_search):
        """Test query with only filters, no search terms."""
        db, auth, messaging, servers, search = db_and_search

        parsed = search.parse_query("from:alice has:image")

        assert len(parsed.filters) == 2
        assert len(parsed.search_terms) == 0

    def test_query_with_empty_filter_value(self, db_and_search):
        """Test query with empty filter value."""
        db, auth, messaging, servers, search = db_and_search

        parsed = search.parse_query("from: hello")

        assert "hello" in parsed.search_terms

    def test_very_long_query(self, db_and_search):
        """Test very long query."""
        db, auth, messaging, servers, search = db_and_search

        long_query = "word " * 100

        parsed = search.parse_query(long_query)

        assert isinstance(parsed, ParsedQuery)

    def test_query_with_multiple_spaces(self, db_and_search):
        """Test query with multiple spaces."""
        db, auth, messaging, servers, search = db_and_search

        parsed = search.parse_query("hello    world")

        assert "hello" in parsed.search_terms
        assert "world" in parsed.search_terms


@pytest.mark.search
class TestSearchEdgeCases:
    """Test search edge cases."""

    def test_search_with_zero_limit(self, db_and_search):
        """Test search with zero limit."""
        db, auth, messaging, servers, search = db_and_search

        results = search.search_messages(1, "test", limit=0)

        assert len(results) == 0

    def test_search_with_large_offset(self, db_and_search):
        """Test search with large offset."""
        db, auth, messaging, servers, search = db_and_search

        results = search.search_messages(1, "test", offset=10000)

        assert len(results) == 0

    def test_search_nonexistent_conversation(self, db_and_search):
        """Test search in nonexistent conversation."""
        db, auth, messaging, servers, search = db_and_search

        results = search.search_messages(1, "test", conversation_id=999999999)

        assert len(results) == 0

    def test_search_nonexistent_server(self, db_and_search):
        """Test search in nonexistent server."""
        db, auth, messaging, servers, search = db_and_search

        results = search.search_messages(1, "test", server_id=999999999)

        assert len(results) == 0


@pytest.mark.search
class TestIndexingEdgeCases:
    """Test indexing edge cases."""

    def test_index_empty_content(self, db_and_search):
        """Test indexing message with empty content."""
        db, auth, messaging, servers, search = db_and_search

        search.index_message(
            message_id=999999,
            content="",
            metadata={"author_id": 1, "conversation_id": 1},
        )

    def test_index_very_long_content(self, db_and_search):
        """Test indexing message with very long content."""
        db, auth, messaging, servers, search = db_and_search

        long_content = "word " * 10000

        search.index_message(
            message_id=999998,
            content=long_content,
            metadata={"author_id": 1, "conversation_id": 1},
        )

    def test_index_content_with_special_chars(self, db_and_search):
        """Test indexing content with special characters."""
        db, auth, messaging, servers, search = db_and_search

        special_content = "Hello @user! Check out https://example.com #hashtag"

        search.index_message(
            message_id=999997,
            content=special_content,
            metadata={"author_id": 1, "conversation_id": 1},
        )

    def test_remove_nonexistent_message(self, db_and_search):
        """Test removing nonexistent message from index."""
        db, auth, messaging, servers, search = db_and_search

        search.remove_from_index(999999999)


@pytest.mark.search
class TestDiscoveryEdgeCases:
    """Test discovery edge cases."""

    def test_list_servers_empty_category(self, db_and_search):
        """Test listing servers in empty category."""
        db, auth, messaging, servers, search = db_and_search

        results = search.list_public_servers(category="nonexistent_category")

        assert len(results) == 0

    def test_list_servers_invalid_sort(self, db_and_search):
        """Test listing servers with invalid sort."""
        db, auth, messaging, servers, search = db_and_search

        results = search.list_public_servers(sort_by="invalid_sort")

        assert isinstance(results, list)

    def test_get_suggestions_empty_query(self, db_and_search):
        """Test getting suggestions for empty query."""
        db, auth, messaging, servers, search = db_and_search

        suggestions = search.get_search_suggestions(1, "")

        assert isinstance(suggestions, list)

    def test_get_suggestions_long_query(self, db_and_search):
        """Test getting suggestions for long query."""
        db, auth, messaging, servers, search = db_and_search

        long_query = "a" * 100

        suggestions = search.get_search_suggestions(1, long_query)

        assert isinstance(suggestions, list)


@pytest.mark.search
class TestConcurrentOperations:
    """Test concurrent operations."""

    def test_multiple_index_operations(self, db_and_search):
        """Test multiple index operations."""
        db, auth, messaging, servers, search = db_and_search

        unique_id = uuid.uuid4().hex[:8]

        for i in range(10):
            search.index_message(
                message_id=900000 + i,
                content=f"concurrent message {i} {unique_id}",
                metadata={"author_id": 1, "conversation_id": 1},
            )

        for i in range(10):
            search.remove_from_index(900000 + i)

    def test_index_update_same_message(self, db_and_search):
        """Test updating same message multiple times."""
        db, auth, messaging, servers, search = db_and_search

        unique_id = uuid.uuid4().hex[:8]
        msg_id = 800000

        for i in range(5):
            search.index_message(
                message_id=msg_id,
                content=f"updated content {i} {unique_id}",
                metadata={"author_id": 1, "conversation_id": 1},
            )


@pytest.mark.search
class TestErrorRecovery:
    """Test error recovery scenarios."""

    def test_search_after_failed_index(self, db_and_search):
        """Test search works after failed index operation."""
        db, auth, messaging, servers, search = db_and_search

        results = search.search_messages(1, "test")

        assert isinstance(results, list)

    def test_module_state_after_error(self, db_and_search):
        """Test module state is consistent after error."""
        db, auth, messaging, servers, search = db_and_search

        try:
            search.search_messages(1, "test", limit=10000)
        except SearchLimitError:
            pass

        results = search.search_messages(1, "test", limit=10)

        assert isinstance(results, list)
