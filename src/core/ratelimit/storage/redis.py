"""
Redis storage backend for rate limiting.
Distributed implementation for multi-instance deployments.
"""

import time
from typing import Optional, List, Dict, Any, cast

from src.core.database.redis_client import get_client, is_available, RedisOperationError
from .base import RateLimitStorage


class RedisStorage(RateLimitStorage):
    """Distributed Redis storage for rate limit buckets."""

    def __init__(self, key_prefix: str = "ratelimit"):
        """
        Initialize Redis storage.

        Args:
            key_prefix: Prefix for all rate limit keys in Redis.
        """
        self._key_prefix = key_prefix

    def _get_key(self, key: str) -> str:
        """Get full Redis key."""
        return f"{self._key_prefix}:{key}"

    def get_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        """Get bucket state by key."""
        client = get_client()
        if not client or not is_available():
            return None

        full_key = self._get_key(key)
        try:
            return cast(Optional[Dict[str, Any]], client.get_json(full_key))
        except RedisOperationError:
            return None

    def set_bucket(
        self, key: str, state: Dict[str, Any], ttl: Optional[float] = None
    ) -> None:
        """Set bucket state."""
        client = get_client()
        if not client or not is_available():
            return

        full_key = self._get_key(key)
        try:
            # Redis TTL is usually an int (seconds), but can be float in some clients (miliseconds)
            # RedisClient.set_json expects Optional[int]
            int_ttl = int(ttl) if ttl is not None else None
            client.set_json(full_key, state, ttl=int_ttl)
        except RedisOperationError:
            pass

    def delete_bucket(self, key: str) -> bool:
        """Delete a bucket."""
        client = get_client()
        if not client or not is_available():
            return False

        full_key = self._get_key(key)
        try:
            return client.delete(full_key) > 0
        except RedisOperationError:
            return False

    def get_keys_by_prefix(self, prefix: str) -> List[str]:
        """Get all keys matching a prefix."""
        client = get_client()
        if not client or not is_available():
            return []

        # get_key adds self._key_prefix
        full_prefix = self._get_key(prefix)
        try:
            # client.keys returns keys relative to client.key_prefix
            keys = client.keys(f"{full_prefix}*")
            return keys
        except RedisOperationError:
            return []

    def delete_by_prefix(self, prefix: str) -> int:
        """Delete all keys matching a prefix."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_prefix = self._get_key(prefix)
        try:
            # client.keys returns keys relative to its own prefix
            keys = client.keys(f"{full_prefix}*")
            if keys:
                # RedisClient.delete expects keys WITHOUT its own prefix,
                # which is what client.keys returns.
                return client.delete(*keys)
            return 0
        except RedisOperationError:
            return 0

    def clear_all(self) -> None:
        """Clear all stored buckets."""
        self.delete_by_prefix("")

    def increment(self, key: str, field: str, amount: int = 1) -> int:
        """Atomically increment a field in a bucket."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_key = self._get_key(key)
        token = client.acquire_lock(full_key)
        if token:
            try:
                bucket = cast(Dict[str, Any], client.get_json(full_key) or {})
                current = bucket.get(field, 0)
                new_value = current + amount
                bucket[field] = new_value
                client.set_json(full_key, bucket)
                return new_value
            except RedisOperationError:
                return 0
            finally:
                if token:  # Re-check for type narrowing
                    client.release_lock(full_key, token)
        return 0

    def get_and_set(self, key: str, field: str, value: Any, default: Any = None) -> Any:
        """Atomically get current value and set new value."""
        client = get_client()
        if not client or not is_available():
            return default

        full_key = self._get_key(key)
        token = client.acquire_lock(full_key)
        if token:
            try:
                bucket = cast(Dict[str, Any], client.get_json(full_key) or {})
                previous = bucket.get(field, default)
                bucket[field] = value
                client.set_json(full_key, bucket)
                return previous
            except RedisOperationError:
                return default
            finally:
                if token:  # Re-check for type narrowing
                    client.release_lock(full_key, token)
        return default

    def add_to_list(
        self, key: str, field: str, value: Any, max_size: int = 1000
    ) -> int:
        """Add value to a list field, maintaining max size."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_key = self._get_key(key)
        token = client.acquire_lock(full_key)
        if token:
            try:
                bucket = cast(Dict[str, Any], client.get_json(full_key) or {})
                lst = bucket.get(field, [])
                if not isinstance(lst, list):
                    lst = []
                lst.append(value)
                if len(lst) > max_size:
                    lst = lst[-max_size:]
                bucket[field] = lst
                client.set_json(full_key, bucket)
                return len(lst)
            except RedisOperationError:
                return 0
            finally:
                if token:  # Re-check for type narrowing
                    client.release_lock(full_key, token)
        return 0

    def trim_list(self, key: str, field: str, min_value: Any) -> int:
        """Remove list items less than min_value."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_key = self._get_key(key)
        token = client.acquire_lock(full_key)
        if token:
            try:
                bucket = cast(Dict[str, Any], client.get_json(full_key) or {})
                lst = bucket.get(field, [])
                if not isinstance(lst, list):
                    return 0
                original_len = len(lst)
                lst = [v for v in lst if v >= min_value]
                bucket[field] = lst
                client.set_json(full_key, bucket)
                return original_len - len(lst)
            except RedisOperationError:
                return 0
            finally:
                if token:  # Re-check for type narrowing
                    client.release_lock(full_key, token)
        return 0

    def acquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        """Acquire a lock for atomic operations."""
        client = get_client()
        if not client or not is_available():
            return None
        return client.acquire_lock(f"extlock:{key}", timeout=timeout)

    def release_lock(self, key: str, token: Optional[str] = None) -> None:
        """Release a lock."""
        client = get_client()
        if not client or not is_available() or token is None:
            return
        client.release_lock(f"extlock:{key}", token)

    def eval_token_bucket(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int = 86400
    ) -> tuple:
        """Atomically evaluate a token bucket using Lua."""
        client = get_client()
        if not client or not is_available():
            # Fallback if Redis fails - fail closed or open?
            # Usually rate limiting fails open to avoid blocking traffic,
            # but for safety let's return a default "allowed"
            return True, capacity, 0.0

        script = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local refill_rate = tonumber(ARGV[2])
        local cost = tonumber(ARGV[3])
        local ttl = tonumber(ARGV[4])
        local now = tonumber(ARGV[5])

        local bucket = redis.call('GET', key)
        local tokens
        local last_update

        if bucket then
            local data = cjson.decode(bucket)
            tokens = tonumber(data.tokens)
            last_update = tonumber(data.last_update)
        else
            tokens = capacity
            last_update = now
        end

        local elapsed = math.max(0, now - last_update)
        tokens = math.min(capacity, tokens + (elapsed * refill_rate))

        local allowed = false
        if tokens >= cost then
            tokens = tokens - cost
            allowed = true
        end

        local res = {
            tokens = tokens,
            last_update = now
        }
        
        redis.call('SET', key, cjson.encode(res), 'EX', ttl)
        
        local reset_after = 0
        if not allowed then
            reset_after = (cost - tokens) / refill_rate
        end
        
        return {tostring(allowed), tostring(math.floor(tokens)), tostring(reset_after)}
        """

        full_key = self._get_key(key)
        try:
            # We use time.time() here because monotonic() isn't shared across machines
            # and Redis Lua scripts don't have access to non-deterministic TIME unless using specialized commands
            now = time.time()
            result = client.eval_lua(
                script, keys=[full_key], args=[capacity, refill_rate, cost, ttl, now]
            )

            allowed = result[0] == "true"
            remaining = int(float(result[1]))
            reset_after = float(result[2])

            return allowed, remaining, reset_after
        except Exception:
            # Log error and fail open
            return True, capacity, 0.0
