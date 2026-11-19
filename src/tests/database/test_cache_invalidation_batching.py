"""Regression: pattern invalidation must stay complete without one giant DEL.

Privilege changes invalidate broad key families (``has_perm:*``,
``user_servers:*``). Deleting every matched key in a single Valkey command
blocks the shared backend for the whole batch. The invalidation is now sent
in bounded chunks; these tests pin that the deletion is still *complete* (no
stale-after-revocation entry survives) and the reported count is unchanged.
"""

import fnmatch
import threading

import pytest

from src.core.database.cache import operations
from src.core.database.cache.base import _mem_cache, mem_cache_set


class FakeValkey:
    """Snapshot-style stand-in: ``keys`` returns a list, like the real client."""

    def __init__(self):
        self.store = {}
        self.delete_batches = []
        self.lock = threading.Lock()

    def keys(self, pattern):
        with self.lock:
            return [k for k in self.store if fnmatch.fnmatch(k, pattern)]

    def delete(self, *keys):
        with self.lock:
            self.delete_batches.append(len(keys))
            for key in keys:
                self.store.pop(key, None)
            return len(keys)


@pytest.fixture
def backend(monkeypatch):
    client = FakeValkey()
    monkeypatch.setattr(operations, "get_client", lambda: client)
    monkeypatch.setattr(operations, "is_available", lambda: True)
    return client


@pytest.fixture(autouse=True)
def _isolate_mem_tier():
    _mem_cache.clear()
    yield
    _mem_cache.clear()


def test_large_pattern_invalidation_is_complete_and_batched(backend):
    total = operations._INVALIDATE_DELETE_BATCH * 2 + 7
    for i in range(total):
        key = f"has_perm:int:{i}:str:manage"
        backend.store[key] = True
        mem_cache_set(key, True, 60)
    # An unrelated key must survive the narrow pattern.
    backend.store["other:keep"] = True
    mem_cache_set("other:keep", True, 60)

    mem_entries = sum(1 for k in _mem_cache if fnmatch.fnmatch(k, "has_perm:*"))
    removed = operations.invalidate_pattern("has_perm:*")

    assert removed == total + mem_entries
    assert backend.store == {"other:keep": True}
    assert [k for k in _mem_cache if k.startswith("has_perm:")] == []
    assert "other:keep" in _mem_cache
    assert len(backend.delete_batches) == 3
    assert max(backend.delete_batches) <= operations._INVALIDATE_DELETE_BATCH
    assert sum(backend.delete_batches) == total


def test_concurrent_invalidations_do_not_lose_deletions(backend):
    per_thread = 60
    for n in range(6):
        for i in range(per_thread):
            key = f"perm:int:{n}:int:{i}"
            backend.store[key] = True
            mem_cache_set(key, True, 60)

    errors = []

    def invalidate(offset):
        try:
            operations.invalidate_pattern(f"perm:int:{offset}:*")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=invalidate, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert backend.store == {}
    assert [k for k in _mem_cache if k.startswith("perm:")] == []
