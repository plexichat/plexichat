"""
Tests for query parser - exhaustive syntax testing.
"""

import pytest
from datetime import datetime, timedelta

from src.core.search.query.parser import QueryParser, parse_query
from src.core.search.models import FilterType


@pytest.mark.search
class TestQueryParserBasic:
    """Test basic query parsing."""

    def test_empty_query(self):
        """Test parsing empty query."""
        result = parse_query("")
        assert result.raw_query == ""
        assert result.search_terms == []
        assert result.filters == []

    def test_whitespace_query(self):
        """Test parsing whitespace-only query."""
        result = parse_query("   ")
        assert result.search_terms == []
        assert result.filters == []

    def test_simple_word(self):
        """Test parsing single word."""
        result = parse_query("hello")
        assert result.search_terms == ["hello"]
        assert result.filters == []

    def test_multiple_words(self):
        """Test parsing multiple words."""
        result = parse_query("hello world test")
        assert result.search_terms == ["hello", "world", "test"]

    def test_search_text_property(self):
        """Test search_text property."""
        result = parse_query("hello world")
        assert result.search_text == "hello world"


@pytest.mark.search
class TestQueryParserFilters:
    """Test filter parsing."""

    def test_from_filter(self):
        """Test from:user filter."""
        result = parse_query("from:alice")
        assert len(result.filters) == 1
        assert result.filters[0].filter_type == FilterType.FROM_USER
        assert result.filters[0].value == "alice"
        assert not result.filters[0].negated

    def test_in_filter(self):
        """Test in:channel filter."""
        result = parse_query("in:general")
        assert len(result.filters) == 1
        assert result.filters[0].filter_type == FilterType.IN_CHANNEL
        assert result.filters[0].value == "general"

    def test_before_filter_iso_date(self):
        """Test before:date filter with ISO date."""
        result = parse_query("before:2024-01-15")
        assert len(result.filters) == 1
        assert result.filters[0].filter_type == FilterType.BEFORE_DATE
        assert result.filters[0].value == "2024-01-15"

    def test_after_filter_iso_date(self):
        """Test after:date filter with ISO date."""
        result = parse_query("after:2024-01-15")
        assert len(result.filters) == 1
        assert result.filters[0].filter_type == FilterType.AFTER_DATE
        assert result.filters[0].value == "2024-01-15"

    def test_before_filter_relative_days(self):
        """Test before:date filter with relative days."""
        result = parse_query("before:7d")
        assert len(result.filters) == 1
        assert result.filters[0].filter_type == FilterType.BEFORE_DATE
        expected = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        assert result.filters[0].value == expected

    def test_after_filter_relative_weeks(self):
        """Test after:date filter with relative weeks."""
        result = parse_query("after:2w")
        assert len(result.filters) == 1
        expected = (datetime.utcnow() - timedelta(weeks=2)).strftime("%Y-%m-%d")
        assert result.filters[0].value == expected

    def test_before_filter_today(self):
        """Test before:today filter."""
        result = parse_query("before:today")
        assert len(result.filters) == 1
        expected = datetime.utcnow().strftime("%Y-%m-%d")
        assert result.filters[0].value == expected

    def test_after_filter_yesterday(self):
        """Test after:yesterday filter."""
        result = parse_query("after:yesterday")
        assert len(result.filters) == 1
        expected = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        assert result.filters[0].value == expected

    def test_has_link_filter(self):
        """Test has:link filter."""
        result = parse_query("has:link")
        assert len(result.filters) == 1
        assert result.filters[0].filter_type == FilterType.HAS_ATTACHMENT
        assert result.filters[0].value == "link"

    def test_has_image_filter(self):
        """Test has:image filter."""
        result = parse_query("has:image")
        assert len(result.filters) == 1
        assert result.filters[0].value == "image"

    def test_has_file_filter(self):
        """Test has:file filter."""
        result = parse_query("has:file")
        assert len(result.filters) == 1
        assert result.filters[0].value == "file"

    def test_has_embed_filter(self):
        """Test has:embed filter."""
        result = parse_query("has:embed")
        assert len(result.filters) == 1
        assert result.filters[0].value == "embed"

    def test_has_invalid_value(self):
        """Test has: with invalid value is ignored."""
        result = parse_query("has:invalid")
        assert len(result.filters) == 0

    def test_mentions_filter(self):
        """Test mentions:user filter."""
        result = parse_query("mentions:bob")
        assert len(result.filters) == 1
        assert result.filters[0].filter_type == FilterType.MENTIONS_USER
        assert result.filters[0].value == "bob"

    def test_pinned_true_filter(self):
        """Test pinned:true filter."""
        result = parse_query("pinned:true")
        assert len(result.filters) == 1
        assert result.filters[0].filter_type == FilterType.PINNED
        assert result.filters[0].value == "true"

    def test_pinned_false_filter(self):
        """Test pinned:false filter."""
        result = parse_query("pinned:false")
        assert len(result.filters) == 1
        assert result.filters[0].value == "false"

    def test_pinned_yes_filter(self):
        """Test pinned:yes normalizes to true."""
        result = parse_query("pinned:yes")
        assert result.filters[0].value == "true"

    def test_pinned_no_filter(self):
        """Test pinned:no normalizes to false."""
        result = parse_query("pinned:no")
        assert result.filters[0].value == "false"


@pytest.mark.search
class TestQueryParserNegation:
    """Test negated filters."""

    def test_negated_from_filter(self):
        """Test -from:user filter."""
        result = parse_query("-from:alice")
        assert len(result.filters) == 1
        assert result.filters[0].filter_type == FilterType.FROM_USER
        assert result.filters[0].value == "alice"
        assert result.filters[0].negated

    def test_negated_has_filter(self):
        """Test -has:link filter."""
        result = parse_query("-has:link")
        assert len(result.filters) == 1
        assert result.filters[0].negated

    def test_negated_in_filter(self):
        """Test -in:channel filter."""
        result = parse_query("-in:general")
        assert len(result.filters) == 1
        assert result.filters[0].negated


@pytest.mark.search
class TestQueryParserExactPhrases:
    """Test exact phrase parsing."""

    def test_single_exact_phrase(self):
        """Test single exact phrase."""
        result = parse_query('"hello world"')
        assert result.exact_phrases == ["hello world"]

    def test_multiple_exact_phrases(self):
        """Test multiple exact phrases."""
        result = parse_query('"hello world" "foo bar"')
        assert result.exact_phrases == ["hello world", "foo bar"]

    def test_exact_phrase_with_terms(self):
        """Test exact phrase mixed with search terms."""
        result = parse_query('test "hello world" query')
        assert result.exact_phrases == ["hello world"]
        assert "test" in result.search_terms
        assert "query" in result.search_terms

    def test_exact_phrase_with_filter(self):
        """Test exact phrase with filter."""
        result = parse_query('from:alice "hello world"')
        assert result.exact_phrases == ["hello world"]
        assert len(result.filters) == 1


@pytest.mark.search
class TestQueryParserComplex:
    """Test complex query combinations."""

    def test_multiple_filters(self):
        """Test multiple filters in one query."""
        result = parse_query("from:alice in:general has:image")
        assert len(result.filters) == 3

    def test_filters_with_search_terms(self):
        """Test filters combined with search terms."""
        result = parse_query("from:alice hello world")
        assert len(result.filters) == 1
        assert result.search_terms == ["hello", "world"]

    def test_complex_query(self):
        """Test complex query with all features."""
        result = parse_query('from:alice -has:link "exact phrase" hello after:7d')

        assert len(result.filters) == 3
        assert result.exact_phrases == ["exact phrase"]
        assert "hello" in result.search_terms

        from_filter = next(
            f for f in result.filters if f.filter_type == FilterType.FROM_USER
        )
        assert from_filter.value == "alice"
        assert not from_filter.negated

        has_filter = next(
            f for f in result.filters if f.filter_type == FilterType.HAS_ATTACHMENT
        )
        assert has_filter.negated

    def test_has_filters_property(self):
        """Test has_filters property."""
        result1 = parse_query("hello world")
        assert not result1.has_filters

        result2 = parse_query("from:alice hello")
        assert result2.has_filters


@pytest.mark.search
class TestQueryParserCaseInsensitivity:
    """Test case insensitivity."""

    def test_filter_name_case_insensitive(self):
        """Test filter names are case insensitive."""
        result1 = parse_query("FROM:alice")
        result2 = parse_query("from:alice")
        result3 = parse_query("From:alice")

        assert result1.filters[0].filter_type == FilterType.FROM_USER
        assert result2.filters[0].filter_type == FilterType.FROM_USER
        assert result3.filters[0].filter_type == FilterType.FROM_USER

    def test_has_value_case_insensitive(self):
        """Test has: values are case insensitive."""
        result1 = parse_query("has:LINK")
        result2 = parse_query("has:Link")

        assert result1.filters[0].value == "link"
        assert result2.filters[0].value == "link"


@pytest.mark.search
class TestQueryParserSuggestions:
    """Test filter suggestions."""

    def test_filter_suggestions_from(self):
        """Test suggestions for 'fr' prefix."""
        parser = QueryParser()
        suggestions = parser.get_filter_suggestions("fr")
        assert "from:" in suggestions

    def test_filter_suggestions_has(self):
        """Test suggestions for 'has:' prefix."""
        parser = QueryParser()
        suggestions = parser.get_filter_suggestions("has:")
        assert "has:link" in suggestions
        assert "has:image" in suggestions

    def test_filter_suggestions_empty(self):
        """Test suggestions for non-matching prefix."""
        parser = QueryParser()
        suggestions = parser.get_filter_suggestions("xyz")
        assert len(suggestions) == 0
