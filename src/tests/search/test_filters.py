"""
Tests for search filters.
"""

import pytest
from datetime import datetime, timedelta

from src.core.search.query.filters import FilterProcessor, apply_filters
from src.core.search.models import (
    ParsedQuery,
    QueryFilter,
    FilterType,
    MessageSearchResult,
)


@pytest.mark.search
class TestFilterProcessor:
    """Test filter processor."""

    def test_no_filters_returns_all(self):
        """Test that no filters returns all results."""
        results = [
            MessageSearchResult(id=1, message_id=1, content="hello", author_id=1, conversation_id=1),
            MessageSearchResult(id=2, message_id=2, content="world", author_id=2, conversation_id=1),
        ]

        parsed = ParsedQuery(raw_query="test", search_terms=["test"])

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 2

    def test_exact_phrase_filter(self):
        """Test exact phrase filtering."""
        results = [
            MessageSearchResult(id=1, message_id=1, content="hello world", author_id=1, conversation_id=1),
            MessageSearchResult(id=2, message_id=2, content="hello there", author_id=2, conversation_id=1),
        ]

        parsed = ParsedQuery(
            raw_query='"hello world"',
            search_terms=[],
            exact_phrases=["hello world"]
        )

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert filtered[0].content == "hello world"

    def test_pinned_filter_true(self):
        """Test pinned:true filter."""
        results = [
            MessageSearchResult(id=1, message_id=1, content="pinned", author_id=1, conversation_id=1, is_pinned=True),
            MessageSearchResult(id=2, message_id=2, content="not pinned", author_id=2, conversation_id=1, is_pinned=False),
        ]

        parsed = ParsedQuery(
            raw_query="pinned:true",
            search_terms=[],
            filters=[QueryFilter(FilterType.PINNED, "true")]
        )

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert filtered[0].is_pinned is True

    def test_pinned_filter_false(self):
        """Test pinned:false filter."""
        results = [
            MessageSearchResult(id=1, message_id=1, content="pinned", author_id=1, conversation_id=1, is_pinned=True),
            MessageSearchResult(id=2, message_id=2, content="not pinned", author_id=2, conversation_id=1, is_pinned=False),
        ]

        parsed = ParsedQuery(
            raw_query="pinned:false",
            search_terms=[],
            filters=[QueryFilter(FilterType.PINNED, "false")]
        )

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert filtered[0].is_pinned is False

    def test_has_attachment_filter(self):
        """Test has:attachment filter."""
        results = [
            MessageSearchResult(id=1, message_id=1, content="with file", author_id=1, conversation_id=1, has_attachments=True),
            MessageSearchResult(id=2, message_id=2, content="no file", author_id=2, conversation_id=1, has_attachments=False),
        ]

        parsed = ParsedQuery(
            raw_query="has:file",
            search_terms=[],
            filters=[QueryFilter(FilterType.HAS_ATTACHMENT, "file")]
        )

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert filtered[0].has_attachments is True

    def test_has_link_filter(self):
        """Test has:link filter."""
        results = [
            MessageSearchResult(id=1, message_id=1, content="check https://example.com", author_id=1, conversation_id=1),
            MessageSearchResult(id=2, message_id=2, content="no link here", author_id=2, conversation_id=1),
        ]

        parsed = ParsedQuery(
            raw_query="has:link",
            search_terms=[],
            filters=[QueryFilter(FilterType.HAS_ATTACHMENT, "link")]
        )

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert "https://" in filtered[0].content

    def test_negated_filter(self):
        """Test negated filter."""
        results = [
            MessageSearchResult(id=1, message_id=1, content="pinned", author_id=1, conversation_id=1, is_pinned=True),
            MessageSearchResult(id=2, message_id=2, content="not pinned", author_id=2, conversation_id=1, is_pinned=False),
        ]

        parsed = ParsedQuery(
            raw_query="-pinned:true",
            search_terms=[],
            filters=[QueryFilter(FilterType.PINNED, "true", negated=True)]
        )

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert filtered[0].is_pinned is False

    def test_before_date_filter(self):
        """Test before:date filter."""
        now = datetime.utcnow()
        old_ts = int((now - timedelta(days=10)).timestamp() * 1000)
        new_ts = int(now.timestamp() * 1000)

        results = [
            MessageSearchResult(id=1, message_id=1, content="old", author_id=1, conversation_id=1, created_at=old_ts),
            MessageSearchResult(id=2, message_id=2, content="new", author_id=2, conversation_id=1, created_at=new_ts),
        ]

        cutoff = (now - timedelta(days=5)).strftime("%Y-%m-%d")

        parsed = ParsedQuery(
            raw_query=f"before:{cutoff}",
            search_terms=[],
            filters=[QueryFilter(FilterType.BEFORE_DATE, cutoff)]
        )

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert filtered[0].content == "old"

    def test_after_date_filter(self):
        """Test after:date filter."""
        now = datetime.utcnow()
        old_ts = int((now - timedelta(days=10)).timestamp() * 1000)
        new_ts = int(now.timestamp() * 1000)

        results = [
            MessageSearchResult(id=1, message_id=1, content="old", author_id=1, conversation_id=1, created_at=old_ts),
            MessageSearchResult(id=2, message_id=2, content="new", author_id=2, conversation_id=1, created_at=new_ts),
        ]

        cutoff = (now - timedelta(days=5)).strftime("%Y-%m-%d")

        parsed = ParsedQuery(
            raw_query=f"after:{cutoff}",
            search_terms=[],
            filters=[QueryFilter(FilterType.AFTER_DATE, cutoff)]
        )

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert filtered[0].content == "new"

    def test_multiple_filters(self):
        """Test multiple filters combined."""
        results = [
            MessageSearchResult(id=1, message_id=1, content="pinned with file", author_id=1, conversation_id=1, is_pinned=True, has_attachments=True),
            MessageSearchResult(id=2, message_id=2, content="pinned no file", author_id=1, conversation_id=1, is_pinned=True, has_attachments=False),
            MessageSearchResult(id=3, message_id=3, content="not pinned with file", author_id=1, conversation_id=1, is_pinned=False, has_attachments=True),
        ]

        parsed = ParsedQuery(
            raw_query="pinned:true has:file",
            search_terms=[],
            filters=[
                QueryFilter(FilterType.PINNED, "true"),
                QueryFilter(FilterType.HAS_ATTACHMENT, "file"),
            ]
        )

        filtered = apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert filtered[0].is_pinned is True
        assert filtered[0].has_attachments is True


@pytest.mark.search
class TestFilterProcessorWithDb:
    """Test filter processor with database lookups."""

    def test_from_filter_with_user_id(self, db_and_modules):
        """Test from: filter with numeric user ID."""
        db, auth, messaging, servers, search = db_and_modules

        results = [
            MessageSearchResult(id=1, message_id=1, content="from user 1", author_id=1, conversation_id=1),
            MessageSearchResult(id=2, message_id=2, content="from user 2", author_id=2, conversation_id=1),
        ]

        parsed = ParsedQuery(
            raw_query="from:1",
            search_terms=[],
            filters=[QueryFilter(FilterType.FROM_USER, "1")]
        )

        processor = FilterProcessor(db)
        filtered = processor.apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert filtered[0].author_id == 1

    def test_mentions_filter(self, db_and_modules):
        """Test mentions: filter."""
        db, auth, messaging, servers, search = db_and_modules

        results = [
            MessageSearchResult(id=1, message_id=1, content="hey <@123> check this", author_id=1, conversation_id=1),
            MessageSearchResult(id=2, message_id=2, content="no mentions here", author_id=2, conversation_id=1),
        ]

        parsed = ParsedQuery(
            raw_query="mentions:123",
            search_terms=[],
            filters=[QueryFilter(FilterType.MENTIONS_USER, "123")]
        )

        processor = FilterProcessor(db)
        filtered = processor.apply_filters(results, parsed, user_id=1)

        assert len(filtered) == 1
        assert "<@123>" in filtered[0].content
