"""
Cache module - Provides caching layer using Redis with in-memory fallback.

This module provides decorator-based caching and manual cache operations
for frequently accessed data like user profiles, server info, and sessions.

Features:
    - Decorator-based caching (@cached)
    - Manual cache get/set/invalidate
    - Automatic key generation from function arguments
    - TTL support with configurable defaults
    - Graceful fallback when Redis is unavailable
    - Cache statistics and monitoring
"""

from .base import CacheError
from .decorators import cached
from .operations import (
    cache_get,
    cache_set,
    cache_delete,
    invalidate_cached,
    invalidate_pattern,
)
from .monitoring import cache_stats, reset_cache_stats, cache_health
from .session import (
    cache_session,
    get_cached_session,
    invalidate_session,
    invalidate_user_sessions,
    invalidate_user_servers,
    invalidate_server,
    invalidate_server_channels,
)
from .presence import (
    cache_presence,
    get_cached_presence,
    get_bulk_presence,
)
from .rate_limit import check_rate_limit, reset_rate_limit

__all__ = [
    "CacheError",
    "cached",
    "cache_get",
    "cache_set",
    "cache_delete",
    "invalidate_cached",
    "invalidate_pattern",
    "cache_stats",
    "reset_cache_stats",
    "cache_health",
    "cache_session",
    "get_cached_session",
    "invalidate_session",
    "invalidate_user_sessions",
    "invalidate_user_servers",
    "invalidate_server",
    "invalidate_server_channels",
    "cache_presence",
    "get_cached_presence",
    "get_bulk_presence",
    "check_rate_limit",
    "reset_rate_limit",
]
