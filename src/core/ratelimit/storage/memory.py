"""
In-memory storage backend for rate limiting.
Thread-safe implementation for single-instance deployments.
"""

import threading
import time
from typing import Optional, List, Dict, Any
from .base import RateLimitStorage


class MemoryStorage(RateLimitStorage):
    """Thread-safe in-memory storage for rate limit buckets."""

    def __init__(self, cleanup_interval: float = 60.0, max_buckets: int = 100000):
        """
        Initialize memory storage.

        Args:
            cleanup_interval: Seconds between cleanup runs.
            max_buckets: Maximum number of buckets to store.
        """
        self._buckets: Dict[str, Dict[str, Any]] = {}
        self._ttls: Dict[str, float] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.RLock()
        self._cleanup_interval = cleanup_interval
        self._max_buckets = max_buckets
        self._last_cleanup = time.monotonic()

    def _maybe_cleanup(self) -> None:
        """Run cleanup if interval has passed."""
        now = time.monotonic()
        if now - self._last_cleanup >= self._cleanup_interval:
            self._cleanup_expired()
            self._last_cleanup = now

    def _cleanup_expired(self) -> None:
        """Remove expired buckets."""
        now = time.monotonic()
        expired_keys = []
        with self._global_lock:
            for key, ttl_time in list(self._ttls.items()):
                if now >= ttl_time:
                    expired_keys.append(key)
            for key in expired_keys:
                self._buckets.pop(key, None)
                self._ttls.pop(key, None)
                self._locks.pop(key, None)
            if len(self._buckets) > self._max_buckets:
                oldest_keys = sorted(
                    self._buckets.keys(),
                    key=lambda k: self._buckets[k].get("last_update", 0),
                )[: len(self._buckets) - self._max_buckets]
                for key in oldest_keys:
                    self._buckets.pop(key, None)
                    self._ttls.pop(key, None)
                    self._locks.pop(key, None)

    def _get_lock(self, key: str) -> threading.Lock:
        """Get or create lock for a key."""
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def get_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        """Get bucket state by key."""
        self._maybe_cleanup()
        with self._global_lock:
            if key in self._ttls:
                if time.monotonic() >= self._ttls[key]:
                    self._buckets.pop(key, None)
                    self._ttls.pop(key, None)
                    return None
            return self._buckets.get(key)

    def set_bucket(
        self, key: str, state: Dict[str, Any], ttl: Optional[float] = None
    ) -> None:
        """Set bucket state."""
        with self._global_lock:
            self._buckets[key] = state
            if ttl is not None:
                self._ttls[key] = time.monotonic() + ttl
            elif key in self._ttls:
                del self._ttls[key]

    def delete_bucket(self, key: str) -> bool:
        """Delete a bucket."""
        with self._global_lock:
            existed = key in self._buckets
            self._buckets.pop(key, None)
            self._ttls.pop(key, None)
            self._locks.pop(key, None)
            return existed

    def get_keys_by_prefix(self, prefix: str) -> List[str]:
        """Get all keys matching a prefix."""
        with self._global_lock:
            return [k for k in self._buckets.keys() if k.startswith(prefix)]

    def delete_by_prefix(self, prefix: str) -> int:
        """Delete all keys matching a prefix."""
        with self._global_lock:
            keys_to_delete = [k for k in self._buckets.keys() if k.startswith(prefix)]
            for key in keys_to_delete:
                self._buckets.pop(key, None)
                self._ttls.pop(key, None)
                self._locks.pop(key, None)
            return len(keys_to_delete)

    def clear_all(self) -> None:
        """Clear all stored buckets."""
        with self._global_lock:
            self._buckets.clear()
            self._ttls.clear()
            self._locks.clear()

    def increment(self, key: str, field: str, amount: int = 1) -> int:
        """Atomically increment a field in a bucket."""
        lock = self._get_lock(key)
        with lock:
            bucket = self._buckets.get(key, {})
            current = bucket.get(field, 0)
            new_value = current + amount
            bucket[field] = new_value
            with self._global_lock:
                self._buckets[key] = bucket
            return new_value

    def get_and_set(self, key: str, field: str, value: Any, default: Any = None) -> Any:
        """Atomically get current value and set new value."""
        lock = self._get_lock(key)
        with lock:
            bucket = self._buckets.get(key, {})
            previous = bucket.get(field, default)
            bucket[field] = value
            with self._global_lock:
                self._buckets[key] = bucket
            return previous

    def add_to_list(
        self, key: str, field: str, value: Any, max_size: int = 1000
    ) -> int:
        """Add value to a list field, maintaining max size."""
        lock = self._get_lock(key)
        with lock:
            bucket = self._buckets.get(key, {})
            lst = bucket.get(field, [])
            if not isinstance(lst, list):
                lst = []
            lst.append(value)
            if len(lst) > max_size:
                lst = lst[-max_size:]
            bucket[field] = lst
            with self._global_lock:
                self._buckets[key] = bucket
            return len(lst)

    def trim_list(self, key: str, field: str, min_value: Any) -> int:
        """Remove list items less than min_value."""
        lock = self._get_lock(key)
        with lock:
            bucket = self._buckets.get(key, {})
            lst = bucket.get(field, [])
            if not isinstance(lst, list):
                return 0
            original_len = len(lst)
            lst = [v for v in lst if v >= min_value]
            bucket[field] = lst
            with self._global_lock:
                self._buckets[key] = bucket
            return original_len - len(lst)

    def acquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        """Acquire a lock for atomic operations."""
        lock = self._get_lock(f"lock:{key}")
        if lock.acquire(timeout=timeout):
            return "locked"
        return None

    def release_lock(self, key: str, token: Optional[str] = None) -> None:
        """Release a lock."""
        lock = self._get_lock(f"lock:{key}")
        try:
            lock.release()
        except RuntimeError:
            pass

    def eval_token_bucket(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int = 86400
    ) -> tuple:
        """Atomically evaluate a token bucket."""
        lock = self._get_lock(key)
        with lock:
            now = time.monotonic()
            bucket = self.get_bucket(key) or {
                "tokens": float(capacity),
                "last_update": now,
            }

            tokens = bucket.get("tokens", float(capacity))
            last_update = bucket.get("last_update", now)

            # Refill
            elapsed = now - last_update
            tokens = min(float(capacity), tokens + elapsed * refill_rate)

            if tokens >= cost:
                tokens -= cost
                allowed = True
            else:
                allowed = False

            bucket["tokens"] = tokens
            bucket["last_update"] = now
            self.set_bucket(key, bucket, ttl=ttl)

            remaining = int(tokens)
            reset_after = (cost - tokens) / refill_rate if not allowed else 0.0

            return allowed, remaining, max(0.0, reset_after)

    def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        with self._global_lock:
            return {
                "bucket_count": len(self._buckets),
                "ttl_count": len(self._ttls),
                "lock_count": len(self._locks),
                "max_buckets": self._max_buckets,
            }
