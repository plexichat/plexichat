"""
Rate limiting helpers.

Provides check_rate_limit and reset_rate_limit functions
with Valkey primary and in-memory fallback.
"""

import threading
import time
from collections import OrderedDict
from typing import List, Tuple

import utils.logger as logger
from src.core.security.hardening import bounded_int
from ..valkey_client import (
    get_client,
    is_available,
    ValkeyOperationError,
)
from .operations import cache_delete

# Global map for in-memory rate limiting when Valkey is unavailable. It is an
# OrderedDict so a hard entry cap can evict the least-recently-used key: the
# previous age-only sweep never removed anything while all counters were fresh,
# letting a flood of distinct keys grow the map without bound (memory DoS).
_mem_rate_limits: "OrderedDict[str, List[float]]" = OrderedDict()
_MEM_RATE_LIMIT_MAX_KEYS = 5000
_mem_rate_limits_lock = threading.Lock()

# Atomic INCR + first-hit EXPIRE. Doing this in Lua closes the race where a
# process died (or EXPIRE failed) between INCR and EXPIRE: previously the
# counter then persisted with NO TTL and the user/IP was rate-limited
# permanently.
_INCR_EXPIRE_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return current
"""


def check_rate_limit(key: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
    """Check if a rate limit has been exceeded.

    Args:
        key: Rate limit key (e.g., "ratelimit:user:123:messages").
        limit: Maximum number of requests allowed.
        window_seconds: Time window in seconds.

    Returns:
        Tuple of (allowed: bool, remaining: int).
    """
    # A negative window makes the timestamp filter `ts > now - window`
    # select nothing (disabling the limiter), and an enormous limit does the
    # same. Bound both before they drive the in-memory/Valkey counter.
    limit = bounded_int(limit, "limit", minimum=1, maximum=1_000_000)
    window_seconds = bounded_int(
        window_seconds, "window_seconds", minimum=1, maximum=86_400
    )

    client = get_client()
    if not client or not is_available():
        global _mem_rate_limits
        full_key = f"ratelimit:{key}"
        now = time.time()

        with _mem_rate_limits_lock:
            # Touch the key first so a still-active counter is never chosen as
            # the eviction victim when the map is at its hard cap.
            if full_key in _mem_rate_limits:
                _mem_rate_limits.move_to_end(full_key)

            if len(_mem_rate_limits) >= _MEM_RATE_LIMIT_MAX_KEYS:
                logger.debug("Cleaning up in-memory rate limit cache")
                # First drop stale counters older than an hour...
                for k in list(_mem_rate_limits.keys()):
                    entry = _mem_rate_limits.get(k)
                    if entry and entry[-1] < now - 3600:
                        del _mem_rate_limits[k]
                # ...then enforce the hard cap even when every counter is
                # fresh, evicting the least-recently-used keys so a burst of
                # distinct keys cannot grow the map without bound.
                while len(_mem_rate_limits) >= _MEM_RATE_LIMIT_MAX_KEYS:
                    _mem_rate_limits.popitem(last=False)

            timestamps = [
                ts
                for ts in _mem_rate_limits.get(full_key, [])
                if ts > now - window_seconds
            ]

            if len(timestamps) < limit:
                timestamps.append(now)
                _mem_rate_limits[full_key] = timestamps
                return True, limit - len(timestamps)
            _mem_rate_limits[full_key] = timestamps
            return False, 0

    full_key = f"ratelimit:{key}"

    try:
        current = int(
            client.eval_lua(_INCR_EXPIRE_SCRIPT, [full_key], [window_seconds])
        )
        remaining = max(0, limit - current)
        allowed = current <= limit
        return allowed, remaining
    except ValkeyOperationError as e:
        logger.warning(f"Rate limit check failed for {key}: {e}")
        # A limiter backend error must not silently reopen an abuse corridor.
        # Preserve the existing fail-closed default; development deployments
        # can explicitly opt into fail-open through the central config.
        try:
            import utils.config as _config

            if bool(
                _config.get("rate_limiting", {}).get(
                    "fail_open_on_backend_error", False
                )
            ):
                return True, limit
        except Exception as feature_error:
            # No feature override means no limit; the reason is still recorded.
            logger.debug(f"rate-limit feature lookup failed: {feature_error}")
        return False, 0


def reset_rate_limit(key: str) -> bool:
    """Reset a rate limit counter.

    Args:
        key: Rate limit key.

    Returns:
        True if reset successfully.
    """
    global _mem_rate_limits
    with _mem_rate_limits_lock:
        _mem_rate_limits.pop(f"ratelimit:{key}", None)
    return cache_delete(f"ratelimit:{key}")
