"""Regression tests for bounded cross-worker event deduplication.

Cross-worker fan-out has no unique message id until this fix, and the
receiver had no dedupe at all.  That makes the pre-identity ``sw="unknown"``
self-echo a guaranteed duplicate delivery, and leaves no defence against a
re-delivered/duplicated pub/sub message.  These tests pin the new id envelope
and the bounded, TTL-expiring dedupe registry on both sides.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.api.websocket.__init__ as ws_mod
import src.core.database as database_module
from src.core.events.dedupe import CrossWorkerDedupe, cross_worker_dedupe
from src.core.events.models import Event
from src.core.events.types import EventType

pytestmark = pytest.mark.unit


def _make_event(data=None):
    return Event(
        event_type=EventType.PRESENCE_UPDATE, data={"k": "v"} if data is None else data
    )


def _valkey_mocks(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(database_module, "valkey_available", lambda: True)
    monkeypatch.setattr(database_module, "get_valkey_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _clean_dedupe():
    """The registry is a process singleton — reset it around every test."""
    cross_worker_dedupe.clear()
    yield
    cross_worker_dedupe.clear()


@pytest.fixture
def _receiver(monkeypatch):
    """Wire an AsyncMock dispatcher and stable worker id into the listener."""
    dispatcher = MagicMock()
    dispatcher.dispatch_remote_event = AsyncMock(return_value=1)
    monkeypatch.setattr(ws_mod, "_dispatcher", dispatcher)
    monkeypatch.setattr(ws_mod, "_get_worker_id", lambda: "worker-A")
    return dispatcher


def _payload(event_id=None, source="worker-B"):
    body = {
        "t": "MESSAGE_CREATE",
        "d": {},
        "u": [101, 102],
        "sw": source,
    }
    if event_id is not None:
        body["id"] = event_id
    return json.dumps(body)


# ── publisher side ─────────────────────────────────────────────────────


def test_publish_adds_unique_id_and_remembers_it(events_module, monkeypatch):
    """Every publish carries a unique id and records it for echo detection."""
    em = events_module._manager
    em.set_worker_id("worker-A")
    client = _valkey_mocks(monkeypatch)

    em._publish_cross_worker(_make_event(), [101])
    em._publish_cross_worker(_make_event(), [102])

    bodies = [json.loads(call[0][1]) for call in client.publish.call_args_list]
    ids = [body.get("id") for body in bodies]
    assert all(ids), f"Every payload needs an id, got {ids}"
    assert ids[0] != ids[1], "Ids must be globally unique per publication"
    assert ids[0].startswith("worker-A:")
    # Both ids are remembered so this worker drops its own echoes.
    for event_id in ids:
        assert cross_worker_dedupe.is_duplicate(event_id) is True


def test_publish_id_survives_legacy_fields(events_module, monkeypatch):
    """Adding ``id`` does not disturb the established envelope contract."""
    em = events_module._manager
    em.set_worker_id("worker-A")
    client = _valkey_mocks(monkeypatch)

    em._publish_cross_worker(_make_event(data={"m": 7}), [101, 102])

    body = json.loads(client.publish.call_args[0][1])
    assert body["t"] == EventType.PRESENCE_UPDATE.value
    assert body["d"] == {"m": 7}
    assert body["u"] == [101, 102]
    assert body["sw"] == "worker-A"
    assert body["id"]


# ── receiver side ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_event_id_dispatched_once(_receiver):
    """The same id delivered twice is dispatched to local clients once."""
    await ws_mod._handle_cross_worker_message(_payload("evt-1"))
    await ws_mod._handle_cross_worker_message(_payload("evt-1"))

    _receiver.dispatch_remote_event.assert_awaited_once_with(
        "MESSAGE_CREATE", {}, [101, 102]
    )
    assert cross_worker_dedupe.metrics()["duplicates"] == 1


@pytest.mark.asyncio
async def test_distinct_event_ids_both_dispatched(_receiver):
    """Dedupe must not suppress genuinely different events."""
    await ws_mod._handle_cross_worker_message(_payload("evt-1"))
    await ws_mod._handle_cross_worker_message(_payload("evt-2"))

    assert _receiver.dispatch_remote_event.await_count == 2


@pytest.mark.asyncio
async def test_own_pre_identity_echo_is_dropped(events_module, monkeypatch, _receiver):
    """The real bug: an ``sw='unknown'`` self-echo is suppressed by id.

    The worker-id guard intentionally cannot drop pre-identity echoes, so
    the shared publish-time record is what prevents a duplicate local send.
    """
    em = events_module._manager
    em.set_worker_id("unknown")
    client = _valkey_mocks(monkeypatch)
    em._publish_cross_worker(_make_event(), [101])
    echoed_id = json.loads(client.publish.call_args[0][1])["id"]

    # The echo arrives tagged "unknown" — the sw guard must not fire, but
    # the dedupe cache must.
    await ws_mod._handle_cross_worker_message(_payload(echoed_id, source="unknown"))

    _receiver.dispatch_remote_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_payload_without_id_still_dispatched(_receiver):
    """Mixed-version workers without an id are not dropped (no check weakened)."""
    await ws_mod._handle_cross_worker_message(_payload(source="worker-B"))

    _receiver.dispatch_remote_event.assert_awaited_once()


# ── bounds ─────────────────────────────────────────────────────────────


def test_cache_is_hard_bounded():
    """The registry never exceeds ``max_entries`` (no retained-message leak)."""
    cache = CrossWorkerDedupe(max_entries=8, ttl_sec=300.0)
    for i in range(1000):
        cache.remember(f"evt-{i}")

    metrics = cache.metrics()
    assert metrics["size"] == 8
    assert metrics["max"] == 8
    assert metrics["evictions"] == 1000 - 8


def test_expired_ids_are_reprocessed():
    """A TTL-expired id is no longer a duplicate (bounded retention window)."""
    cache = CrossWorkerDedupe(max_entries=16, ttl_sec=0.01)
    assert cache.is_duplicate("evt-old") is False
    time.sleep(0.02)
    assert cache.is_duplicate("evt-old") is False
    assert cache.metrics()["size"] == 1


def test_metrics_counters_are_bounded_without_weakening_dedupe():
    """Regression: observability counters must not grow without reset.

    The registry's ``_seen`` set is hard-bounded, but the ``duplicates`` and
    ``evictions`` metrics used to be plain ``int`` counters that climbed for
    the whole process lifetime (only the test-only ``clear`` reset them).
    They now saturate at ``max_counter`` and expose explicit ``*_saturated``
    flags so a value is always an exact count or a documented lower bound.

    Crucially, saturation must NOT weaken the dedupe check itself: duplicates
    keep being recognised after the counter caps, and genuinely new ids are
    still accepted on first sight.
    """
    cap = 5
    cache = CrossWorkerDedupe(max_entries=4, ttl_sec=300.0, max_counter=cap)

    # --- duplicate counter saturates -----------------------------------
    assert cache.is_duplicate("evt-seen") is False  # first sight stores
    for _ in range(50):
        assert cache.is_duplicate("evt-seen") is True  # still a duplicate
    dup_metrics = cache.metrics()
    assert dup_metrics["duplicates"] == cap
    assert dup_metrics["max_counter"] == cap
    assert dup_metrics["duplicates_saturated"] is True
    assert dup_metrics["evictions_saturated"] is False

    # A new id is still accepted even after the duplicate counter capped.
    assert cache.is_duplicate("evt-fresh") is False

    # --- eviction counter saturates ------------------------------------
    for i in range(100):
        cache.remember(f"evt-{i}")
    evict_metrics = cache.metrics()
    assert evict_metrics["evictions"] == cap
    assert evict_metrics["evictions_saturated"] is True
    # The retention bound still holds exactly (no weakening of the registry).
    assert evict_metrics["size"] == 4
    assert evict_metrics["max"] == 4

    # --- clear() resets both counters and their saturation flags -------
    cache.clear()
    reset = cache.metrics()
    assert reset["duplicates"] == 0
    assert reset["evictions"] == 0
    assert reset["duplicates_saturated"] is False
    assert reset["evictions_saturated"] is False
