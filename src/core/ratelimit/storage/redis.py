"""
Redis storage backend for rate limiting.
Distributed implementation for multi-instance deployments.
"""

import time
from typing import Optional, List, Dict, Any

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
            return client.get_json(full_key)
        except RedisOperationError:
            return None

    def set_bucket(self, key: str, state: Dict[str, Any], ttl: Optional[float] = None) -> None:
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

        full_prefix = self._get_key(prefix)
        try:
            keys = client.keys(f"{full_prefix}*")
            # Remove our prefix from the keys
            prefix_len = len(self._get_key(""))
            return [k[prefix_len:] for k in keys]
        except RedisOperationError:
            return []

    def delete_by_prefix(self, prefix: str) -> int:
        """Delete all keys matching a prefix."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_prefix = self._get_key(prefix)
        try:
            keys = client.keys(f"{full_prefix}*")
            if keys:
                # RedisClient.keys returns keys WITHOUT its own prefix, 
                # but client.delete expects the keys as passed to it (which it will prefix).
                # Actually, RedisClient.keys implementation:
                # return [k[prefix_len:] if k.startswith(self.key_prefix) else k for k in keys]
                # So the keys are relative to the RedisClient prefix.
                # client.delete prefixes them again. 
                # This is a bit recursive with my _get_key.
                
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
        if client.acquire_lock(full_key):
            try:
                bucket = client.get_json(full_key) or {}
                current = bucket.get(field, 0)
                new_value = current + amount
                bucket[field] = new_value
                client.set_json(full_key, bucket)
                return new_value
            except RedisOperationError:
                return 0
            finally:
                client.release_lock(full_key)
        return 0

    def get_and_set(
        self,
        key: str,
        field: str,
        value: Any,
        default: Any = None
    ) -> Any:
        """Atomically get current value and set new value."""
        client = get_client()
        if not client or not is_available():
            return default

        full_key = self._get_key(key)
        if client.acquire_lock(full_key):
            try:
                bucket = client.get_json(full_key) or {}
                previous = bucket.get(field, default)
                bucket[field] = value
                client.set_json(full_key, bucket)
                return previous
            except RedisOperationError:
                return default
            finally:
                client.release_lock(full_key)
        return default

    def add_to_list(self, key: str, field: str, value: Any, max_size: int = 1000) -> int:
        """Add value to a list field, maintaining max size."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_key = self._get_key(key)
        if client.acquire_lock(full_key):
            try:
                bucket = client.get_json(full_key) or {}
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
                client.release_lock(full_key)
        return 0

    def trim_list(self, key: str, field: str, min_value: Any) -> int:
        """Remove list items less than min_value."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_key = self._get_key(key)
        if client.acquire_lock(full_key):
            try:
                bucket = client.get_json(full_key) or {}
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
                client.release_lock(full_key)
        return 0

    def acquire_lock(self, key: str, timeout: float = 1.0) -> bool:
        """Acquire a lock for atomic operations."""
        client = get_client()
        if not client or not is_available():
            return False
        return client.acquire_lock(f"extlock:{key}", timeout=timeout)

    def release_lock(self, key: str) -> None:
        """Release a lock."""
        client = get_client()
        if not client or not is_available():
            return
        client.release_lock(f"extlock:{key}")
