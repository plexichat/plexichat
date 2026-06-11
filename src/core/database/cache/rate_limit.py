"""
Rate limiting helpers.

Provides check_rate_limit and reset_rate_limit functions
with Redis primary and in-memory fallback.
"""

import time
from typing import Dict, List, Tuple

import utils.logger as logger
from ..redis_client import (
    get_client,
    is_available,
    RedisOperationError,
)
from .operations import cache_delete

# Global dictionary for in-memory rate limiting when Redis is unavailable
_mem_rate_limits: Dict[str, List[float]] = {}


def check_rate_limit(key: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
    """Check if a rate limit has been exceeded.

    Args:
        key: Rate limit key (e.g., "ratelimit:user:123:messages").
        limit: Maximum number of requests allowed.
        window_seconds: Time window in seconds.

    Returns:
        Tuple of (allowed: bool, remaining: int).
    """
    client = get_client()
    if not client or not is_available():
        global _mem_rate_limits
        full_key = f"ratelimit:{key}"
        now = time.time()

        if len(_mem_rate_limits) > 5000:
            logger.debug("Cleaning up in-memory rate limit cache")
            for k in list(_mem_rate_limits.keys()):
                if _mem_rate_limits[k] and _mem_rate_limits[k][-1] < now - 3600:
                    del _mem_rate_limits[k]

        timestamps = _mem_rate_limits.get(full_key, [])
        timestamps = [ts for ts in timestamps if ts > now - window_seconds]

        if len(timestamps) < limit:
            timestamps.append(now)
            _mem_rate_limits[full_key] = timestamps
            return True, limit - len(timestamps)
        else:
            _mem_rate_limits[full_key] = timestamps
            return False, 0

    full_key = f"ratelimit:{key}"

    try:
        current = client.incr(full_key)
        if current == 1:
            client.expire(full_key, window_seconds)
        remaining = max(0, limit - current)
        allowed = current <= limit
        return allowed, remaining
    except RedisOperationError as e:
        logger.warning(f"Rate limit check failed for {key}: {e}")
        return True, limit


def reset_rate_limit(key: str) -> bool:
    """Reset a rate limit counter.

    Args:
        key: Rate limit key.

    Returns:
        True if reset successfully.
    """
    global _mem_rate_limits
    _mem_rate_limits.pop(f"ratelimit:{key}", None)
    return cache_delete(f"ratelimit:{key}")
