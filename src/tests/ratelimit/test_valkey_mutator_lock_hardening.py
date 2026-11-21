"""Regression: ValkeyStorage's counter mutators must lock, fail closed, and
serialize under multi-worker contention.

Two real scaling/security weaknesses are pinned here:

1. The four public mutators (``increment``/``get_and_set``/``add_to_list``/
   ``trim_list``) and their async twins used to SILENTLY FAIL OPEN when the
   distributed lock could not be acquired -- they returned ``0``/``default``.
   An unacquired lock means the read-modify-write never happened, so an abuse
   counter (upload bytes, sticker/friend-request quotas) under-counts and an
   attacker's budget is multiplied by the number of contending workers.
   They now raise ``RateLimitStoreUnavailable`` (fail closed).

2. The blocking mutators acquired the lock with NO explicit timeout, relying
   on the client's implicit default (10s) while every other lock in the module
   passes 5.0. A stalled lock could park a worker indefinitely. Every mutator
   now passes an explicit, finite, bounded acquire timeout.

The concurrency test drives the real lock path with a thread-safe fake client
so a lost update fails the test rather than passing against a permissive stub.
"""

import secrets
import threading
from typing import Any, Dict, List, Optional, Tuple

import pytest

import src.core.ratelimit.storage.valkey as valkey_module
from src.core.ratelimit.exceptions import RateLimitStoreUnavailable
from src.core.ratelimit.storage import ValkeyStorage

_MUTATORS = [
    ("increment", ("field", 1)),
    ("get_and_set", ("field", 1)),
    ("add_to_list", ("field", 1)),
    ("trim_list", ("field", 1)),
]

_ASYNC_MUTATORS = [
    ("aincrement", ("field", 1)),
    ("aget_and_set", ("field", 1)),
    ("aadd_to_list", ("field", 1)),
    ("atrim_list", ("field", 1)),
]


class _ThreadSafeValkeyClient:
    """In-memory fake whose locks are real ``threading.Lock`` objects.

    A token is minted per acquisition and verified on release, so a late
    holder cannot free a lock another worker now owns (the production client
    does the same via its compare-and-delete Lua script).
    """

    def __init__(self) -> None:
        self.data: Dict[str, Dict[str, Any]] = {}
        self.timeouts: List[Tuple[str, float]] = []
        self.get_calls = 0
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._token_by_key: Dict[str, str] = {}

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def acquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        self.timeouts.append(("sync", timeout))
        lock = self._lock_for(key)
        if not lock.acquire(timeout=timeout):
            return None
        token = secrets.token_hex(8)
        self._token_by_key[key] = token
        return token

    def release_lock(self, key: str, token: str) -> None:
        if self._token_by_key.get(key) != token:
            raise AssertionError("release attempted with an invalid lock token")
        self._token_by_key.pop(key, None)
        self._lock_for(key).release()

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        self.get_calls += 1
        value = self.data.get(key)
        return dict(value) if value is not None else None

    def set_json(self, key: str, state: Dict[str, Any], ttl: Any = None) -> None:
        self.data[key] = dict(state)

    # Best-effort key-cap pruning reads these; keep them harmless.
    def keys(self, pattern: str) -> List[str]:
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        return [k for k in self.data if k.startswith(prefix)]

    def ttl(self, key: str) -> int:
        return 86400

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                removed += 1
        return removed


class _AsyncThreadSafeValkeyClient(_ThreadSafeValkeyClient):
    async def aacquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        lock = self._lock_for(key)
        if not lock.acquire(timeout=timeout):
            self.timeouts.append(("async", timeout))
            return None
        token = secrets.token_hex(8)
        self._token_by_key[key] = token
        self.timeouts.append(("async", timeout))
        return token

    async def arelease_lock(self, key: str, token: str) -> None:
        self.release_lock(key, token)

    async def aget_json(self, key: str) -> Optional[Dict[str, Any]]:
        return self.get_json(key)

    async def aset_json(self, key: str, state: Dict[str, Any], ttl: Any = None) -> None:
        self.set_json(key, state, ttl)

    async def akeys(self, pattern: str = "*") -> List[str]:
        return self.keys(pattern)

    async def attl(self, key: str) -> int:
        return self.ttl(key)

    async def adelete(self, *keys: str) -> int:
        return self.delete(*keys)


class _NoLockClient(_ThreadSafeValkeyClient):
    """Models a lock service that cannot grant the lock (timeout/outage)."""

    def acquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        self.timeouts.append(("sync", timeout))
        return None

    async def aacquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        self.timeouts.append(("async", timeout))
        return None


def _wire(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(valkey_module, "get_client", lambda: client)
    monkeypatch.setattr(valkey_module, "is_available", lambda: True)


class TestMutatorLockIsBounded:
    @pytest.mark.parametrize("method,args", _MUTATORS)
    def test_blocking_mutators_pass_explicit_bounded_timeout(
        self, monkeypatch: pytest.MonkeyPatch, method: str, args: tuple
    ) -> None:
        client = _ThreadSafeValkeyClient()
        _wire(monkeypatch, client)

        getattr(ValkeyStorage(), method)("user:7", *args)

        assert client.timeouts == [("sync", ValkeyStorage._MUTATOR_LOCK_TIMEOUT)]
        assert 0 < client.timeouts[0][1] < 10.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,args", _ASYNC_MUTATORS)
    async def test_async_mutators_pass_explicit_bounded_timeout(
        self, monkeypatch: pytest.MonkeyPatch, method: str, args: tuple
    ) -> None:
        client = _AsyncThreadSafeValkeyClient()
        _wire(monkeypatch, client)

        await getattr(ValkeyStorage(), method)("user:7", *args)

        assert client.timeouts == [("async", ValkeyStorage._MUTATOR_LOCK_TIMEOUT)]
        assert 0 < client.timeouts[0][1] < 10.0


class TestMutatorsFailClosedWithoutLock:
    @pytest.mark.parametrize("method,args", _MUTATORS)
    def test_blocking_mutator_refuses_to_run_without_the_lock(
        self, monkeypatch: pytest.MonkeyPatch, method: str, args: tuple
    ) -> None:
        client = _NoLockClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()

        with pytest.raises(
            RateLimitStoreUnavailable, match="distributed rate-limit lock"
        ):
            getattr(storage, method)("user:7", *args)

        # No read/write happened: the failure is loud, never a silent 0.
        assert client.get_calls == 0
        assert client.data == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,args", _ASYNC_MUTATORS)
    async def test_async_mutator_refuses_to_run_without_the_lock(
        self, monkeypatch: pytest.MonkeyPatch, method: str, args: tuple
    ) -> None:
        client = _NoLockClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()

        with pytest.raises(
            RateLimitStoreUnavailable, match="distributed rate-limit lock"
        ):
            await getattr(storage, method)("user:7", *args)

        assert client.get_calls == 0
        assert client.data == {}


class TestMutatorConcurrency:
    def test_concurrent_increments_do_not_lose_updates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lock must serialize N workers so all N deductions are applied."""
        client = _ThreadSafeValkeyClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()

        workers = 24
        barrier = threading.Barrier(workers)
        returned: List[int] = []
        returned_guard = threading.Lock()

        def worker() -> None:
            barrier.wait(timeout=10)
            value = storage.increment("abuse:counter", "bytes", 1)
            with returned_guard:
                returned.append(value)

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert all(not thread.is_alive() for thread in threads), "a worker deadlocked"

        final = storage.get_bucket("abuse:counter") or {}
        assert final["bytes"] == workers
        # Every caller observed the value its locked round actually committed.
        assert sorted(returned) == list(range(1, workers + 1))

    def test_concurrent_writers_do_not_clobber_each_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """increment + add_to_list on one key must both survive contention."""
        client = _ThreadSafeValkeyClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()

        workers = 16
        barrier = threading.Barrier(workers)

        def worker(index: int) -> None:
            barrier.wait(timeout=10)
            if index % 2:
                storage.increment("shared:bucket", "hits", 1)
            else:
                storage.add_to_list("shared:bucket", "events", index, max_size=100)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert all(not thread.is_alive() for thread in threads)

        state = storage.get_bucket("shared:bucket") or {}
        assert state["hits"] == workers // 2
        assert sorted(state["events"]) == list(range(0, workers, 2))
