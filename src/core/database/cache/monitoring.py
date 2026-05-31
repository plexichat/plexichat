"""
Cache monitoring functions: stats, health, reset.

Provides cache_stats, reset_cache_stats, and cache_health functions.
"""

from typing import Any, Dict

from ..redis_client import get_client, is_available
from .decorators import get_cache_stats, reset_cache_stats_internal


def cache_stats() -> Dict[str, int]:
    """Get cache statistics."""
    return get_cache_stats().copy()


def reset_cache_stats() -> None:
    """Reset cache statistics."""
    reset_cache_stats_internal()


def cache_health() -> Dict[str, Any]:
    """Get cache health information."""
    client = get_client()
    stats = get_cache_stats()

    result: Dict[str, Any] = {
        "available": is_available(),
        "stats": stats.copy(),
    }

    if client:
        result["redis"] = client.health_check()

    total = stats["hits"] + stats["misses"]
    if total > 0:
        result["hit_rate"] = round(stats["hits"] / total * 100, 2)
    else:
        result["hit_rate"] = 0.0

    return result
