"""
Filter processor - Apply parsed filters to search results.
"""

from typing import List, Dict, Any, Optional, Callable
from datetime import datetime

from ..models import ParsedQuery, QueryFilter, FilterType, MessageSearchResult


class FilterProcessor:
    """Process and apply search filters to results."""
    
    def __init__(self, db=None, auth_module=None, servers_module=None):
        self._db = db
        self._auth = auth_module
        self._servers = servers_module
        self._user_cache: Dict[str, int] = {}
        self._channel_cache: Dict[str, int] = {}
    
    def apply_filters(
        self,
        results: List[MessageSearchResult],
        parsed_query: ParsedQuery,
        user_id: int,
    ) -> List[MessageSearchResult]:
        """
        Apply all filters from parsed query to results.
        
        Args:
            results: List of search results to filter
            parsed_query: Parsed query with filters
            user_id: ID of user performing search
            
        Returns:
            Filtered list of results
        """
        if not parsed_query.has_filters and not parsed_query.exact_phrases:
            return results
        
        filtered = results
        
        for query_filter in parsed_query.filters:
            filtered = self._apply_single_filter(filtered, query_filter, user_id)
        
        for phrase in parsed_query.exact_phrases:
            filtered = [
                r for r in filtered
                if phrase.lower() in r.content.lower()
            ]
        
        return filtered
    
    def _apply_single_filter(
        self,
        results: List[MessageSearchResult],
        query_filter: QueryFilter,
        user_id: int,
    ) -> List[MessageSearchResult]:
        """Apply a single filter to results."""
        filter_func = self._get_filter_function(query_filter, user_id)
        if not filter_func:
            return results
        
        if query_filter.negated:
            return [r for r in results if not filter_func(r)]
        return [r for r in results if filter_func(r)]
    
    def _get_filter_function(
        self,
        query_filter: QueryFilter,
        user_id: int,
    ) -> Optional[Callable[[MessageSearchResult], bool]]:
        """Get the filter function for a filter type."""
        filter_type = query_filter.filter_type
        value = query_filter.value
        
        if filter_type == FilterType.FROM_USER:
            target_user_id = self._resolve_user(value)
            if target_user_id is None:
                return lambda r: False
            return lambda r, uid=target_user_id: r.author_id == uid
        
        if filter_type == FilterType.IN_CHANNEL:
            target_channel_id = self._resolve_channel(value)
            if target_channel_id is None:
                return lambda r: False
            return lambda r, cid=target_channel_id: r.channel_id == cid
        
        if filter_type == FilterType.BEFORE_DATE:
            timestamp = self._date_to_timestamp(value)
            if timestamp is None:
                return None
            return lambda r, ts=timestamp: r.created_at < ts
        
        if filter_type == FilterType.AFTER_DATE:
            timestamp = self._date_to_timestamp(value)
            if timestamp is None:
                return None
            return lambda r, ts=timestamp: r.created_at > ts
        
        if filter_type == FilterType.HAS_ATTACHMENT:
            return self._get_has_filter(value)
        
        if filter_type == FilterType.MENTIONS_USER:
            target_user_id = self._resolve_user(value)
            if target_user_id is None:
                return lambda r: False
            return lambda r, uid=target_user_id: self._check_mentions(r, uid)
        
        if filter_type == FilterType.PINNED:
            is_pinned = value.lower() == "true"
            return lambda r, p=is_pinned: r.is_pinned == p
        
        return None
    
    def _get_has_filter(self, value: str) -> Callable[[MessageSearchResult], bool]:
        """Get filter function for has: filter."""
        value_lower = value.lower()
        
        if value_lower in ("attachment", "file"):
            return lambda r: r.has_attachments
        
        if value_lower == "link":
            return lambda r: self._has_link(r.content)
        
        if value_lower == "image":
            return lambda r: self._has_image(r)
        
        if value_lower == "video":
            return lambda r: self._has_video(r)
        
        if value_lower == "audio":
            return lambda r: self._has_audio(r)
        
        if value_lower == "embed":
            return lambda r: self._has_embed(r)
        
        return lambda r: r.has_attachments
    
    def _resolve_user(self, identifier: str) -> Optional[int]:
        """Resolve username or user ID to user ID."""
        if identifier in self._user_cache:
            return self._user_cache[identifier]
        
        try:
            user_id = int(identifier)
            self._user_cache[identifier] = user_id
            return user_id
        except ValueError:
            pass
        
        if self._db:
            row = self._db.fetch_one(
                "SELECT id FROM auth_users WHERE username = ? COLLATE NOCASE",
                (identifier,)
            )
            if row:
                self._user_cache[identifier] = row["id"]
                return row["id"]
        
        return None
    
    def _resolve_channel(self, identifier: str) -> Optional[int]:
        """Resolve channel name or ID to channel ID."""
        if identifier in self._channel_cache:
            return self._channel_cache[identifier]
        
        try:
            channel_id = int(identifier)
            self._channel_cache[identifier] = channel_id
            return channel_id
        except ValueError:
            pass
        
        if self._db:
            row = self._db.fetch_one(
                "SELECT id FROM srv_channels WHERE name = ? COLLATE NOCASE",
                (identifier,)
            )
            if row:
                self._channel_cache[identifier] = row["id"]
                return row["id"]
        
        return None
    
    def _date_to_timestamp(self, date_str: str) -> Optional[int]:
        """Convert date string to timestamp in milliseconds."""
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    
    def _has_link(self, content: str) -> bool:
        """Check if content contains a link."""
        if not content:
            return False
        return "http://" in content or "https://" in content
    
    def _has_image(self, result: MessageSearchResult) -> bool:
        """Check if message has image attachment."""
        return result.has_attachments
    
    def _has_video(self, result: MessageSearchResult) -> bool:
        """Check if message has video attachment."""
        return result.has_attachments
    
    def _has_audio(self, result: MessageSearchResult) -> bool:
        """Check if message has audio attachment."""
        return result.has_attachments
    
    def _has_embed(self, result: MessageSearchResult) -> bool:
        """Check if message has embed."""
        return result.has_attachments
    
    def _check_mentions(self, result: MessageSearchResult, user_id: int) -> bool:
        """Check if message mentions a user."""
        content = result.content or ""
        return f"<@{user_id}>" in content or f"<@!{user_id}>" in content


def apply_filters(
    results: List[MessageSearchResult],
    parsed_query: ParsedQuery,
    user_id: int,
    db=None,
    auth_module=None,
    servers_module=None,
) -> List[MessageSearchResult]:
    """
    Apply filters to search results.
    
    Convenience function using FilterProcessor.
    """
    processor = FilterProcessor(db, auth_module, servers_module)
    return processor.apply_filters(results, parsed_query, user_id)
