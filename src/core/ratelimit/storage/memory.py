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
        # ``_lock_max`` bounds the orphan Lock pool so a long-running
        # process with heavy churn cannot leak entries indefinitely.
        # Set to 1.5x bucket limit so legitimate churn gets headroom
        # before eviction kicks in.
        self._lock_max = max(2, max_buckets + max_buckets // 2)
        self._last_cleanup = time.monotonic()

    def _maybe_cleanup(self) -> None:
        """Run cleanup if interval has passed."""
        now = time.monotonic()
        if now - self._last_cleanup >= self._cleanup_interval:
            self._cleanup_expired()
            self._last_cleanup = now

    def _cleanup_expired(self) -> None:
        """Remove expired buckets.

        SECURITY: the cleanup path used to also pop ``self._locks``,
        which let two concurrently-arriving callers re-create the
        per-key lock under the false impression they were serialising.
        We now keep every lock for the lifetime of the backend, but
        cap ``_locks`` separately using a generation counter so the
        dict cannot grow without bound. Entries whose keys no longer
        have a bucket are pruned (no callers we still need to bind).
        """
        now = time.monotonic()
        expired_keys = []
        with self._global_lock:
            for key, ttl_time in list(self._ttls.items()):
                if now >= ttl_time:
                    expired_keys.append(key)
            for key in expired_keys:
                self._buckets.pop(key, None)
                self._ttls.pop(key, None)
                # Intentional: do NOT pop ``self._locks`` here. See
                # the docstring for the race-condition rationale.
            if len(self._buckets) > self._max_buckets:
                oldest_keys = sorted(
                    self._buckets.keys(),
                    key=lambda k: self._buckets[k].get("last_update", 0),
                )[: len(self._buckets) - self._max_buckets]
                for key in oldest_keys:
                    self._buckets.pop(key, None)
                    self._ttls.pop(key, None)
                    # Same rationale as above.            # Cap the orphan-lock pool. We can safely drop entries
            # whose keys no longer have a bucket — those locks have
            # no callers we still need to serialise against.
            # Snapshot the keys view into a list first so the
            # subsequent .pop() loop never mutates a live
            # ``dict.keys()`` view (defensive, belt-and-braces).
            # ``_global_lock`` already serialises concurrent writers,
            # but the snapshot is cheap and removes any doubt.
            # Drops amortise: only ``len(_locks) - _lock_max // 4``
            # orphans are pruned per pass so a churn spike converges
            # over several cleanup cycles instead of a single hot
            # sweep.
            if len(self._locks) > self._lock_max:
                orphan_keys = [
                    k for k in list(self._locks.keys()) if k not in self._buckets
                ]
                if orphan_keys:
                    overflow = max(0, len(self._locks) - self._lock_max)
                    drop_quota = max(1, overflow // 4 + 1)
                    for k in orphan_keys[:drop_quota]:
                        self._locks.pop(k, None)

    def _get_lock(self, key: str) -> threading.Lock:
        """Get or create lock for a key."""
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def get_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        """Get bucket state by key.

        PER-KEY LOCK: previously held ``self._global_lock`` (a
        reentrant lock) for the entire read, serialising every rate
        limit lookup across the process. That made the read path
        a contention hot-spot under burst traffic. We now hold the
        per-key lock only long enough to check TTL and snapshot the
        bucket reference, which is enough because:
          - ``self._buckets`` / ``self._ttls`` mutation happens only
            under ``_global_lock`` (cleanup, ``delete_bucket``,
            etc.), so an atomic ``.get()`` snapshot is consistent;
          - the per-key lock guarantees no concurrent
            ``eval_token_bucket`` / ``increment`` is mid-mutation for
            this same key during the snapshot.
        Net effect: reads no longer contend with each other
        process-wide.
        """
        self._maybe_cleanup()
        lock = self._get_lock(key)
        with lock:
            ttl_time = self._ttls.get(key)
            if ttl_time is not None and time.monotonic() >= ttl_time:
                # Eviction happens under the global lock so a
                # concurrent ``delete_bucket`` from cleanup can't
                # race against us here.
                with self._global_lock:
                    if key in self._ttls and time.monotonic() >= self._ttls[key]:
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
        """Delete a bucket.

        SECURITY: do NOT pop ``self._locks[key]`` here. Doing so
        would let the next concurrent caller re-create the per-key
        lock under the false impression that fresh state was being
        protected, while the previous holder is still blocked on the
        dropped Lock object. The lock stays until the bucket re-uses
        the same key (which is harmless; both threads will acquire
        the same Lock).
        """
        with self._global_lock:
            existed = key in self._buckets
            self._buckets.pop(key, None)
            self._ttls.pop(key, None)
            # ``self._locks``: preserved on purpose.
            return existed

    def get_keys_by_prefix(self, prefix: str) -> List[str]:
        """Get all keys matching a prefix."""
        with self._global_lock:
            return [k for k in self._buckets.keys() if k.startswith(prefix)]

    def delete_by_prefix(self, prefix: str) -> int:
        """Delete all keys matching a prefix.

        SECURITY: see :meth:`delete_bucket` for the rationale on
        preserving the per-key locks.
        """
        with self._global_lock:
            keys_to_delete = [k for k in self._buckets.keys() if k.startswith(prefix)]
            for key in keys_to_delete:
                self._buckets.pop(key, None)
                self._ttls.pop(key, None)
                # ``self._locks``: preserved on purpose.
            return len(keys_to_delete)

    def clear_all(self) -> None:
        """Clear all stored buckets.

        SECURITY: ``self._locks`` is preserved across ``clear_all``
        to match the rationale in :meth:`delete_bucket`. If a thread
        is mid-acquire on a Lock for an evicted key, the next
        ``_get_lock`` call MUST hand back the same Lock instance to
        keep serialisation working. Wiping ``_locks`` here would
        create a window where two callers race against the same
        logical key on different Lock objects.
        """
        with self._global_lock:
            self._buckets.clear()
            self._ttls.clear()
            # ``self._locks``: preserved on purpose.

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
        """Atomically evaluate a token bucket with copy-on-write state.

        SECURITY: the previous implementation forwarded through
        ``self.get_bucket`` (which only briefly holds ``_global_lock``)
        and ``self.set_bucket`` (which REPLACES the entire state
        dict). Two concurrent ``increment`` + ``eval_token_bucket``
        callers could each see / write the *shared* dict reference,
        so the second writer's ``set_bucket`` clobbered any
        additional fields the first writer had merged in
        (lost-update). We now build a fresh state dict under the
        per-key lock, mutate only the bucket-relevant fields, and
        install the fresh dict atomically under both locks — no
        field from outside ``eval_token_bucket`` can be lost even if
        the bucket is concurrently ``increment``-ed.
        """
        lock = self._get_lock(key)
        with lock:
            now = time.monotonic()
            with self._global_lock:
                existing = self._buckets.get(key)
            bucket: Dict[str, Any] = dict(existing) if existing is not None else {}

            tokens = float(bucket.get("tokens", float(capacity)))
            last_update = float(bucket.get("last_update", now))

            elapsed = now - last_update
            tokens = min(float(capacity), tokens + elapsed * refill_rate)

            if tokens >= cost:
                tokens -= cost
                allowed = True
            else:
                allowed = False

            bucket["tokens"] = tokens
            bucket["last_update"] = now
            with self._global_lock:
                self._buckets[key] = bucket
                if ttl is not None:
                    self._ttls[key] = now + ttl
                else:
                    self._ttls.pop(key, None)

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
