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


# Key namespaces that back security-critical (bearer-credential) budgets. A
# limiter backend fault must deny for these even when an operator enabled the
# global ``rate_limiting.fail_open_on_backend_error`` escape hatch for ordinary
# product limiters. Without this, that single config line would silently reopen
# online brute force of invite codes with no code change to the invite route.
_FAIL_CLOSED_KEY_PREFIXES = ("invites:redeem-guess:",)


def check_rate_limit(key: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
    """Check if a rate limit has been exceeded.

    Args:
        key: Rate limit key (e.g., "ratelimit:user:123:messages").
        limit: Maximum number of requests allowed.
        window_seconds: Time window in seconds.

    Returns:
        Tuple of (allowed: bool, remaining: int).

    On a limiter backend fault, keys under ``_FAIL_CLOSED_KEY_PREFIXES`` always
    deny; other keys honour the operator-wide ``fail_open_on_backend_error``
    override (legacy behaviour).
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
        # Security-critical namespaces NEVER fail open: an operator enabling the
        # global override for ordinary limits cannot reopen invite-code brute
        # force with a config-only change. Only legacy, non-critical keys even
        # consult that override.
        fail_open = not key.startswith(_FAIL_CLOSED_KEY_PREFIXES)
        if fail_open:
            try:
                import utils.config as _config

                fail_open = bool(
                    _config.get("rate_limiting", {}).get(
                        "fail_open_on_backend_error", False
                    )
                )
            except Exception as feature_error:
                # No feature override means no limit; the reason is recorded.
                logger.debug(f"rate-limit feature lookup failed: {feature_error}")
                fail_open = False
        if fail_open:
            return True, limit
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


def reset_rate_limit_prefix(prefix: str) -> int:
    """Reset every rate-limit counter whose key starts with ``prefix``.

    ``reset_rate_limit`` needs the exact key, which callers that no longer
    track the ids they limited (for example the search manager, whose budget
    is now a shared atomic counter) cannot supply. This drops the whole
    namespace instead.

    Args:
        prefix: Key prefix, without the ``ratelimit:`` storage prefix.

    Returns:
        Number of in-process counters dropped.
    """
    from src.core.database.cache.operations import invalidate_pattern

    global _mem_rate_limits
    storage_prefix = f"ratelimit:{prefix}"
    removed = 0
    with _mem_rate_limits_lock:
        stale = [k for k in _mem_rate_limits if k.startswith(storage_prefix)]
        for stale_key in stale:
            _mem_rate_limits.pop(stale_key, None)
        removed = len(stale)
    invalidate_pattern(f"{storage_prefix}*")
    return removed
