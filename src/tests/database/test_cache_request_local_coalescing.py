"""Regression tests for request-local coalescing of repeated cache lookups.

One request frequently resolves the same read-only key from several layers.
These tests pin the invariant that the identical lookup hits the backing store
at most once per request scope, that misses and invalidations are handled, and
that code outside a request scope keeps the original pass-through behaviour.
"""

import sys
import time
import types

import pytest

from src.core.database.cache import operations
from src.core.database.cache.base import _mem_cache
from src.core.database.cache.operations import cache_delete, cache_set
from src.core.database.cache.presence import cache_presence, get_cached_presence
from src.core.database.cache.request_local import (
    coalesced_cache_get,
    request_cache_scope,
)
from src.core.database.cache.session import get_cached_session


class CountingValkey:
    """Minimal client that records the backing-store reads we want to collapse."""

    def __init__(self):
        self.store = {}
        self.get_calls = 0

    def get_json(self, key):
        self.get_calls += 1
        return self.store.get(key)

    def set_json(self, key, value, ttl=None):
        self.store[key] = value

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    def sadd(self, *args):
        return 1

    def srem(self, *args):
        return 1


@pytest.fixture(autouse=True)
def _isolated_backend(monkeypatch):
    """Force the fallback (explicit) scope so tests are independent of ASGI state."""
    monkeypatch.setitem(
        sys.modules,
        "src.api.middleware.request_context",
        types.SimpleNamespace(current_context=lambda: None),
    )
    client = CountingValkey()
    monkeypatch.setattr(operations, "get_client", lambda: client)
    monkeypatch.setattr(operations, "is_available", lambda: True)
    _mem_cache.clear()
    yield client
    _mem_cache.clear()


def test_repeated_identical_lookups_hit_backend_once(monkeypatch, _isolated_backend):
    cache_set("session:s1", {"user_id": 1}, ttl=60)
    cache_set("presence:7", {"user_id": 7, "status": "online"}, ttl=60)

    with request_cache_scope():
        sessions = [get_cached_session("s1") for _ in range(10)]
        presences = [get_cached_presence(7) for _ in range(10)]

    assert sessions == [{"user_id": 1}] * 10
    assert presences == [{"user_id": 7, "status": "online"}] * 10
    # Without coalescing this would be 20 GETs (one per lookup).
    assert _isolated_backend.get_calls == 2


def test_miss_result_is_coalesced(_isolated_backend):
    with request_cache_scope():
        results = [get_cached_session("missing") for _ in range(5)]

    assert results == [None] * 5
    assert _isolated_backend.get_calls == 1


def test_no_request_scope_keeps_pass_through_behaviour(_isolated_backend):
    cache_set("presence:9", {"user_id": 9}, ttl=60)

    values = [get_cached_presence(9) for _ in range(4)]

    assert values == [{"user_id": 9}] * 4
    assert _isolated_backend.get_calls == 4


def test_delete_invalidates_coalesced_value(_isolated_backend):
    cache_set("session:s2", {"user_id": 2}, ttl=60)

    with request_cache_scope():
        assert get_cached_session("s2")["user_id"] == 2
        cache_delete("session:s2")
        assert get_cached_session("s2") is None

    assert _isolated_backend.get_calls == 2


def test_write_invalidates_coalesced_value(_isolated_backend):
    with request_cache_scope():
        cache_presence(11, "away")
        first = get_cached_presence(11)
        cache_presence(11, "online")
        second = get_cached_presence(11)

    assert first["status"] == "away"
    assert second["status"] == "online"
    assert _isolated_backend.get_calls == 2


def test_scopes_are_isolated(_isolated_backend):
    cache_set("presence:13", {"user_id": 13}, ttl=60)

    with request_cache_scope():
        get_cached_presence(13)
        get_cached_presence(13)
    assert _isolated_backend.get_calls == 1

    with request_cache_scope():
        get_cached_presence(13)
    assert _isolated_backend.get_calls == 2


def test_asgi_request_context_is_used_when_bound(_isolated_backend, monkeypatch):
    ctx = types.SimpleNamespace()
    monkeypatch.setitem(
        sys.modules,
        "src.api.middleware.request_context",
        types.SimpleNamespace(current_context=lambda: ctx),
    )
    cache_set("presence:21", {"user_id": 21}, ttl=60)

    for _ in range(4):
        assert get_cached_presence(21) == {"user_id": 21}

    assert _isolated_backend.get_calls == 1
    assert len(ctx._cache_lookups) == 1


def test_coalesced_value_has_bounded_lifetime(_isolated_backend, monkeypatch):
    import src.core.database.cache.request_local as request_local

    monkeypatch.setattr(request_local, "_COALESCE_MAX_AGE_SECONDS", 0.01)
    cache_set("presence:31", {"user_id": 31}, ttl=60)

    with request_cache_scope():
        get_cached_presence(31)
        time.sleep(0.05)
        get_cached_presence(31)

    assert _isolated_backend.get_calls == 2


def test_custom_loader_is_called_once(_isolated_backend):
    calls = []

    def loader():
        calls.append(1)
        return "value"

    with request_cache_scope():
        assert coalesced_cache_get("k", loader=loader) == "value"
        assert coalesced_cache_get("k", loader=loader) == "value"

    assert calls == [1]
    assert _isolated_backend.get_calls == 0


def test_coalescing_store_is_bounded_in_long_lived_scope(_isolated_backend):
    """A WebSocket scope outlives one request; its memo must not grow forever.

    Every distinct ``presence:<user>``/``session:<id>`` lookup used to add a new
    key to the scope dict and stay there. A connection that renders fan-out for
    thousands of users therefore pinned thousands of memoised values for the
    life of the socket. The store is now a bounded LRU: repeated keys stay hot
    and the oldest entries are evicted once the cap is reached.
    """
    import src.core.database.cache.request_local as request_local

    cap = getattr(request_local, "_COALESCE_MAX_ENTRIES", None)
    assert cap is not None, "request-local coalescing must have a hard entry cap"
    assert 0 < cap <= 1024

    with request_cache_scope() as store:
        for i in range(cap * 4):
            assert coalesced_cache_get(
                f"presence:{i}", loader=lambda i=i: {"u": i}
            ) == {"u": i}
        assert len(store) <= cap, (
            f"memo grew to {len(store)} entries in one scope (cap {cap})"
        )

        # The most recently used keys survive the sweep: a repeat lookup of a
        # hot key must still coalesce (no second backing read).
        hot_key = f"presence:{cap * 4 - 1}"
        reads_before = _isolated_backend.get_calls
        assert coalesced_cache_get(hot_key, loader=lambda: {"u": -1}) == {
            "u": cap * 4 - 1
        }
        assert _isolated_backend.get_calls == reads_before


def test_evicted_entries_are_recomputed_and_never_cross_users(_isolated_backend):
    """Eviction drops the memo only; a reload returns the right user's value.

    A bounded store must fail closed: an evicted key is recomputed from its own
    loader (never served from another key's stale copy), so one credential's
    presence can never leak into another's read.
    """
    import src.core.database.cache.request_local as request_local

    cap = request_local._COALESCE_MAX_ENTRIES
    with request_cache_scope() as store:
        for i in range(cap + 5):
            coalesced_cache_get(f"presence:{i}", loader=lambda i=i: {"u": i})

        # User 0 was evicted (it was inserted first); looking it up again must
        # call its loader again and return its own fresh value, never a stale
        # copy belonging to another user's key.
        assert coalesced_cache_get("presence:0", loader=lambda: {"u": "reloaded"}) == {
            "u": "reloaded"
        }
        assert store["presence:0"][1] == {"u": "reloaded"}

        # A key that was not evicted still holds its own user's value.
        newest = f"presence:{cap + 4}"
        assert store[newest][1] == {"u": cap + 4}
