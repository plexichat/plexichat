"""
Query parser - Parse advanced search queries with filters.

Supports:
- from:user - Messages from a specific user
- in:channel - Messages in a specific channel
- before:date - Messages before a date
- after:date - Messages after a date
- has:link/image/file/embed - Messages with attachments
- reaction:emoji - Messages with specific reaction
- mentions:user - Messages mentioning a user
- pinned:true/false - Pinned messages
- "exact phrase" - Exact phrase matching
- -filter:value - Negated filters
"""

import re
from typing import List, Optional
from datetime import datetime, timedelta, timezone

from ..models import ParsedQuery, QueryFilter, FilterType


FILTER_PATTERNS = {
    "from": FilterType.FROM_USER,
    "in": FilterType.IN_CHANNEL,
    "before": FilterType.BEFORE_DATE,
    "after": FilterType.AFTER_DATE,
    "has": FilterType.HAS_ATTACHMENT,
    "reaction": FilterType.HAS_REACTION,
    "mentions": FilterType.MENTIONS_USER,
    "pinned": FilterType.PINNED,
}

HAS_VALUES = {"link", "image", "file", "embed", "video", "audio", "attachment"}

DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
]

RELATIVE_DATE_PATTERN = re.compile(r"^(\d+)(d|w|m|y)$", re.IGNORECASE)

# The two normalization regexes are derived from the (module-level) filter
# pattern config. They used to be rebuilt in ``QueryParser.__init__``, so
# every call to the ``parse_query`` convenience helper re-joined
# FILTER_PATTERNS and paid an ``re.compile`` cache lookup. They are
# immutable and stateless, so a single shared pair is safe for every
# parser instance, including the shared default parser below.
_FILTER_REGEX = re.compile(
    r"(-?)(" + "|".join(FILTER_PATTERNS.keys()) + r'):(\S+|"[^"]*")',
    re.IGNORECASE,
)
_PHRASE_REGEX = re.compile(r'"([^"]+)"')

# A relative filter ("7d", "2w") is a human convenience, but the digit run is
# attacker-controlled and ``int()`` parses arbitrarily large values. Passing
# those straight to ``timedelta`` raises OverflowError (C int conversion) out
# of parse(), turning a search request into a 500. Clamp the resolved span to
# a century so it stays inside datetime's range and remains semantically
# meaningful; anything larger is the same "everything" filter as 100y.
MAX_RELATIVE_DATE_DAYS = 36525  # ~100 years


class QueryParser:
    """Parser for advanced search queries with filters."""

    def __init__(self):
        self._filter_regex = _FILTER_REGEX
        self._phrase_regex = _PHRASE_REGEX

    def parse(self, query: str) -> ParsedQuery:
        """
        Parse a search query into structured components.

        Args:
            query: Raw query string

        Returns:
            ParsedQuery with filters and search terms
        """
        if not query or not query.strip():
            return ParsedQuery(raw_query=query, search_terms=[], filters=[])

        query = query.strip()
        filters = []
        exact_phrases = []

        # Each normalization regex used to be applied twice: ``finditer``
        # collected the phrases/filters and a second ``sub`` stripped them
        # from the query. A replacement callback collects and strips in one
        # pass, halving this work on every search request.
        def _collect_phrase(match):
            phrase = match.group(1).strip()
            if phrase:
                exact_phrases.append(phrase)
            return ""

        remaining = self._phrase_regex.sub(_collect_phrase, query)

        def _collect_filter(match):
            negated = match.group(1) == "-"
            filter_name = match.group(2).lower()
            value = match.group(3).strip('"')

            filter_type = FILTER_PATTERNS.get(filter_name)
            if filter_type:
                validated_value = self._validate_filter_value(filter_type, value)
                if validated_value is not None:
                    filters.append(
                        QueryFilter(
                            filter_type=filter_type,
                            value=validated_value,
                            negated=negated,
                        )
                    )
            return ""

        remaining = self._filter_regex.sub(_collect_filter, remaining)

        # Normalize each token once. The previous comprehension called the
        # pure ``str.strip()`` twice on the same immutable token (once as the
        # predicate, once as the emitted value); this runs on every search
        # request and scaled with the token count.
        search_terms = []
        for term in remaining.split():
            stripped = term.strip()
            if stripped and not term.startswith("-:"):
                search_terms.append(stripped)

        return ParsedQuery(
            raw_query=query,
            search_terms=search_terms,
            filters=filters,
            exact_phrases=exact_phrases,
        )

    def _validate_filter_value(
        self, filter_type: FilterType, value: str
    ) -> Optional[str]:
        """Validate and normalize filter value."""
        if not value:
            return None

        if filter_type == FilterType.HAS_ATTACHMENT:
            normalized = value.lower()
            if normalized not in HAS_VALUES:
                return None
            return normalized

        if filter_type == FilterType.PINNED:
            normalized = value.lower()
            if normalized in ("true", "yes", "1"):
                return "true"
            if normalized in ("false", "no", "0"):
                return "false"
            return None

        if filter_type in (FilterType.BEFORE_DATE, FilterType.AFTER_DATE):
            return self._parse_date(value)

        return value

    def _parse_date(self, value: str) -> Optional[str]:
        """Parse date value to ISO format."""
        value = value.strip()

        relative_match = RELATIVE_DATE_PATTERN.match(value)
        if relative_match:
            amount = int(relative_match.group(1))
            unit = relative_match.group(2).lower()

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if unit == "d":
                days = amount
            elif unit == "w":
                days = amount * 7
            elif unit == "m":
                days = amount * 30
            elif unit == "y":
                days = amount * 365
            else:
                return None

            # Bound before timedelta: an unbounded digit string would raise
            # OverflowError here and 500 the whole search request.
            target = now - timedelta(days=min(days, MAX_RELATIVE_DATE_DAYS))
            return target.strftime("%Y-%m-%d")

        if value.lower() == "today":
            return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        if value.lower() == "yesterday":
            return (
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
            ).strftime("%Y-%m-%d")

        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue

        return value

    def get_filter_suggestions(self, partial: str) -> List[str]:
        """Get filter suggestions for partial input."""
        suggestions = []
        partial_lower = partial.lower()

        for filter_name in FILTER_PATTERNS.keys():
            if filter_name.startswith(partial_lower):
                suggestions.append(f"{filter_name}:")

        if partial_lower.startswith("has:"):
            prefix = partial[4:]
            for has_val in HAS_VALUES:
                if has_val.startswith(prefix.lower()):
                    suggestions.append(f"has:{has_val}")

        return suggestions


# The parser is stateless (all state is local to ``parse``), so one shared
# instance serves every convenience call. Constructing a parser per call
# rebuilt the filter pattern string and repeated the regex cache lookups.
_default_parser = QueryParser()


def parse_query(query: str) -> ParsedQuery:
    """
    Parse a search query into structured components.

    Convenience function using a shared default parser.

    Args:
        query: Raw query string

    Returns:
        ParsedQuery with filters and search terms
    """
    return _default_parser.parse(query)
