"""
Query parser - Parse advanced search queries with filters.

Supports:
- from:user - Messages from a specific user
- in:channel - Messages in a specific channel
- before:date - Messages before a date
- after:date - Messages after a date
- has:link/image/file/embed - Messages with attachments
- mentions:user - Messages mentioning a user
- pinned:true/false - Pinned messages
- "exact phrase" - Exact phrase matching
- -filter:value - Negated filters
"""

import re
from typing import List, Tuple, Optional
from datetime import datetime, timedelta

from ..models import ParsedQuery, QueryFilter, FilterType
from ..exceptions import InvalidQuerySyntaxError


FILTER_PATTERNS = {
    "from": FilterType.FROM_USER,
    "in": FilterType.IN_CHANNEL,
    "before": FilterType.BEFORE_DATE,
    "after": FilterType.AFTER_DATE,
    "has": FilterType.HAS_ATTACHMENT,
    "mentions": FilterType.MENTIONS_USER,
    "pinned": FilterType.PINNED,
}

HAS_VALUES = {"link", "image", "file", "embed", "video", "audio", "attachment"}

DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m-%d-%Y",
    "%m/%d/%Y",
]

RELATIVE_DATE_PATTERN = re.compile(r"^(\d+)(d|w|m|y)$", re.IGNORECASE)


class QueryParser:
    """Parser for advanced search queries with filters."""
    
    def __init__(self):
        self._filter_regex = re.compile(
            r'(-?)(' + '|'.join(FILTER_PATTERNS.keys()) + r'):(\S+|"[^"]*")',
            re.IGNORECASE
        )
        self._phrase_regex = re.compile(r'"([^"]+)"')
    
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
        remaining = query
        
        for match in self._phrase_regex.finditer(query):
            phrase = match.group(1).strip()
            if phrase:
                exact_phrases.append(phrase)
        remaining = self._phrase_regex.sub("", remaining)
        
        for match in self._filter_regex.finditer(remaining):
            negated = match.group(1) == "-"
            filter_name = match.group(2).lower()
            value = match.group(3).strip('"')
            
            filter_type = FILTER_PATTERNS.get(filter_name)
            if filter_type:
                validated_value = self._validate_filter_value(filter_type, value)
                if validated_value is not None:
                    filters.append(QueryFilter(
                        filter_type=filter_type,
                        value=validated_value,
                        negated=negated
                    ))
        
        remaining = self._filter_regex.sub("", remaining)
        
        search_terms = [
            term.strip() 
            for term in remaining.split() 
            if term.strip() and not term.startswith("-:")
        ]
        
        return ParsedQuery(
            raw_query=query,
            search_terms=search_terms,
            filters=filters,
            exact_phrases=exact_phrases
        )
    
    def _validate_filter_value(self, filter_type: FilterType, value: str) -> Optional[str]:
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
            
            now = datetime.utcnow()
            if unit == "d":
                target = now - timedelta(days=amount)
            elif unit == "w":
                target = now - timedelta(weeks=amount)
            elif unit == "m":
                target = now - timedelta(days=amount * 30)
            elif unit == "y":
                target = now - timedelta(days=amount * 365)
            else:
                return None
            
            return target.strftime("%Y-%m-%d")
        
        if value.lower() == "today":
            return datetime.utcnow().strftime("%Y-%m-%d")
        if value.lower() == "yesterday":
            return (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        
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


def parse_query(query: str) -> ParsedQuery:
    """
    Parse a search query into structured components.
    
    Convenience function using default parser.
    
    Args:
        query: Raw query string
        
    Returns:
        ParsedQuery with filters and search terms
    """
    parser = QueryParser()
    return parser.parse(query)
