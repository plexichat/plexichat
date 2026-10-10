"""
Abstract base class for rate limit storage backends.

Backends implement the synchronous operations; the ``a``-prefixed
awaitable forms are the interface the event loop uses. See the class
docstring for how the default implementations are derived.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Callable, Optional, List, Dict, Any


class RateLimitStorage(ABC):
    """Abstract base class for rate limit storage.

    Two interfaces, one backend:

    * the synchronous methods (``get_bucket``, ``eval_token_bucket``, ...)
      are what subclasses implement;
    * the awaitable ``a*`` methods are what the ASGI middleware and any
      other event-loop caller uses.

    The default ``a*`` implementation offloads the matching synchronous
    method with :func:`asyncio.to_thread`, which is correct for any
    blocking backend without extra code. Backends override them when they
    can do better: :class:`~.memory.MemoryStorage` calls straight through
    (in-process state cannot block the loop) and
    :class:`~.valkey.ValkeyStorage` uses its native async client.

    Composite operations (``update_bucket``) must be offloaded **whole**,
    never as separate get/set awaits -- the backend's lock lives inside
    the synchronous call, so splitting it would reintroduce the lost-
    update race it exists to prevent.
    """

    @abstractmethod
    def get_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get bucket state by key.

        Args:
            key: Bucket identifier.

        Returns:
            Bucket state dictionary or None if not found.
        """
        pass

    @abstractmethod
    def set_bucket(
        self, key: str, state: Dict[str, Any], ttl: Optional[float] = None
    ) -> None:
        """
        Set bucket state.

        Args:
            key: Bucket identifier.
            state: Bucket state dictionary.
            ttl: Time-to-live in seconds (optional).
        """
        pass

    @abstractmethod
    def delete_bucket(self, key: str) -> bool:
        """
        Delete a bucket.

        Args:
            key: Bucket identifier.

        Returns:
            True if deleted, False if not found.
        """
        pass

    @abstractmethod
    def get_keys_by_prefix(self, prefix: str) -> List[str]:
        """
        Get all keys matching a prefix.

        Args:
            prefix: Key prefix to match.

        Returns:
            List of matching keys.
        """
        pass

    @abstractmethod
    def delete_by_prefix(self, prefix: str) -> int:
        """
        Delete all keys matching a prefix.

        Args:
            prefix: Key prefix to match.

        Returns:
            Number of keys deleted.
        """
        pass

    @abstractmethod
    def clear_all(self) -> None:
        """Clear all stored buckets."""
        pass

    @abstractmethod
    def increment(self, key: str, field: str, amount: int = 1) -> int:
        """
        Atomically increment a field in a bucket.

        Args:
            key: Bucket identifier.
            field: Field name to increment.
            amount: Amount to increment by.

        Returns:
            New value after increment.
        """
        pass

    @abstractmethod
    def get_and_set(self, key: str, field: str, value: Any, default: Any = None) -> Any:
        """
        Atomically get current value and set new value.

        Args:
            key: Bucket identifier.
            field: Field name.
            value: New value to set.
            default: Default if field doesn't exist.

        Returns:
            Previous value.
        """
        pass

    @abstractmethod
    def add_to_list(
        self, key: str, field: str, value: Any, max_size: int = 1000
    ) -> int:
        """
        Add value to a list field, maintaining max size.

        Args:
            key: Bucket identifier.
            field: List field name.
            value: Value to add.
            max_size: Maximum list size.

        Returns:
            New list size.
        """
        pass

    @abstractmethod
    def trim_list(self, key: str, field: str, min_value: Any) -> int:
        """
        Remove list items less than min_value.

        Args:
            key: Bucket identifier.
            field: List field name.
            min_value: Minimum value to keep.

        Returns:
            Number of items removed.
        """
        pass

    @abstractmethod
    def acquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        """
        Acquire a lock for atomic operations.

        Args:
            key: Lock key.
            timeout: Lock timeout in seconds.

        Returns:
            Lock token if acquired, None otherwise.
        """
        pass

    @abstractmethod
    def release_lock(self, key: str, token: Optional[str] = None) -> None:
        """
        Release a lock.

        Args:
            key: Lock key.
            token: Lock token from acquire_lock.
        """
        pass

    @abstractmethod
    def eval_token_bucket(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int = 86400
    ) -> tuple:
        """
        Atomically evaluate a token bucket.

        Args:
            key: Bucket key.
            capacity: Maximum tokens in bucket.
            refill_rate: Tokens to refill per second.
            cost: Tokens to consume.
            ttl: Bucket TTL in seconds.

        Returns:
            Tuple of (allowed: bool, remaining: int, reset_after: float).
        """
        pass

    def update_bucket(
        self,
        key: str,
        mutate_fn: Callable[[Dict[str, Any]], None],
        ttl: Optional[float] = None,
    ) -> None:
        """Read-modify-write a bucket atomically.

        Default implementation holds the backend's distributed lock
        (``acquire_lock``/``release_lock``) across the
        get_bucket()->mutate->set_bucket() sequence so concurrent
        requests cannot lose each other's updates. Backends with a
        cheaper native mechanism (e.g. MemoryStorage's per-key lock)
        override this method.

        Args:
            key: Bucket key.
            mutate_fn: Callable that mutates the state dict in place.
            ttl: Optional TTL to apply when writing back.
        """
        token: Optional[str] = None
        try:
            token = self.acquire_lock(key, timeout=5.0)
        except Exception:
            token = None
        try:
            state = self.get_bucket(key) or {}
            mutate_fn(state)
            self.set_bucket(key, state, ttl=ttl)
        finally:
            if token:
                try:
                    self.release_lock(key, token)
                except Exception:
                    pass

    def close(self) -> None:
        """Close storage connection (optional override)."""
        pass

    # ------------------------------------------------------------------
    # Async API (see the class docstring)
    # ------------------------------------------------------------------

    async def aget_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_bucket, key)

    async def aset_bucket(
        self, key: str, state: Dict[str, Any], ttl: Optional[float] = None
    ) -> None:
        await asyncio.to_thread(self.set_bucket, key, state, ttl)

    async def adelete_bucket(self, key: str) -> bool:
        return await asyncio.to_thread(self.delete_bucket, key)

    async def aget_keys_by_prefix(self, prefix: str) -> List[str]:
        return await asyncio.to_thread(self.get_keys_by_prefix, prefix)

    async def adelete_by_prefix(self, prefix: str) -> int:
        return await asyncio.to_thread(self.delete_by_prefix, prefix)

    async def aclear_all(self) -> None:
        await asyncio.to_thread(self.clear_all)

    async def aincrement(self, key: str, field: str, amount: int = 1) -> int:
        return await asyncio.to_thread(self.increment, key, field, amount)

    async def aget_and_set(
        self, key: str, field: str, value: Any, default: Any = None
    ) -> Any:
        return await asyncio.to_thread(self.get_and_set, key, field, value, default)

    async def aadd_to_list(
        self, key: str, field: str, value: Any, max_size: int = 1000
    ) -> int:
        return await asyncio.to_thread(self.add_to_list, key, field, value, max_size)

    async def atrim_list(self, key: str, field: str, min_value: Any) -> int:
        return await asyncio.to_thread(self.trim_list, key, field, min_value)

    async def aacquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        return await asyncio.to_thread(self.acquire_lock, key, timeout)

    async def arelease_lock(self, key: str, token: Optional[str] = None) -> None:
        await asyncio.to_thread(self.release_lock, key, token)

    async def aeval_token_bucket(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int = 86400
    ) -> tuple:
        return await asyncio.to_thread(
            self.eval_token_bucket, key, capacity, refill_rate, cost, ttl
        )

    async def aupdate_bucket(
        self,
        key: str,
        mutate_fn: Callable[[Dict[str, Any]], None],
        ttl: Optional[float] = None,
    ) -> None:
        await asyncio.to_thread(self.update_bucket, key, mutate_fn, ttl)

    async def aclose(self) -> None:
        """Async counterpart of :meth:`close`."""
        await asyncio.to_thread(self.close)
