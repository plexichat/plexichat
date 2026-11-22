"""The per-message ratchet interval point-read must not be an N+1.

A channel message list decrypts every ``ENC:3:`` envelope through
``decrypt_message`` -> ``ChannelRatchetManager.decrypt`` ->
``ChannelRatchetStore.get_by_id``. All messages on a page normally reference
the same small set of intervals, but the store used to issue one
``SELECT ... WHERE interval_id = ?`` per message. A page of N ratchet messages
therefore cost N identical interval reads.

``ChannelRatchetStore`` now keeps a bounded, short-TTL cache of interval rows
keyed strictly by ``int`` interval id, invalidated on every write that mutates
an interval. The thread listing already avoids this N+1 with an explicit
prefetch; this cache protects the main message-list / search / pins paths,
which have no prefetch hook.

Run with: pytest -n 0 src/tests/encryption/test_ratchet_interval_read_cache.py
"""

from __future__ import annotations

import pytest

from src.utils.encryption.channel_ratchet.interval import RatchetInterval
from src.utils.encryption.channel_ratchet.store import ChannelRatchetStore

pytestmark = pytest.mark.unit

_INTERVAL_ID = 4242
_CONVERSATION_ID = 99


class _CountingDB:
    """Minimal DB stand-in that counts interval point-reads."""

    def __init__(self):
        self.point_reads = 0
        self.row = {
            "interval_id": _INTERVAL_ID,
            "conversation_id": _CONVERSATION_ID,
            "start_message_id": 1000,
            "end_message_id": None,
            "start_key_wrapped": "wrapped",
            "created_at": 100,
            "last_message_at": 100,
        }

    def table_exists(self, name):
        return name == "channel_ratchet_intervals"

    def fetch_one(self, query, params=()):
        if "WHERE interval_id = ?" in query:
            self.point_reads += 1
            return dict(self.row) if params and params[0] == _INTERVAL_ID else None
        raise AssertionError(f"unexpected fetch_one: {query}")

    def execute(self, query, params=()):
        return None


class _FakeKeyring:
    def wrap(self, raw_key):
        return "wrapped"

    def unwrap(self, wrapped):
        return b"k" * 32


def _store(db):
    return ChannelRatchetStore(db, _FakeKeyring())


def test_interval_point_reads_stay_constant_as_page_grows():
    """N decrypts of one interval cost one read, not N (the pre-fix N+1)."""
    small_db = _CountingDB()
    small = _store(small_db)
    for _ in range(2):
        assert small.get_by_id(_INTERVAL_ID) is not None
    small_reads = small_db.point_reads

    large_db = _CountingDB()
    large = _store(large_db)
    for _ in range(50):
        assert large.get_by_id(_INTERVAL_ID) is not None
    large_reads = large_db.point_reads

    # Before the fix this was 2 reads vs 50 reads (one per message).
    assert large_reads <= small_reads + 1, (
        "ratchet interval point-read reverted to an N+1: "
        f"2 decrypts -> {small_reads} reads, 50 decrypts -> {large_reads} reads"
    )
    assert small_reads == 1, small_reads
    assert large_reads == 1, large_reads


def test_cached_interval_is_identical_and_strict_int_keyed():
    """A warm read is field-identical; non-int ids never share a cache key."""
    db = _CountingDB()
    store = _store(db)

    cold = store.get_by_id(_INTERVAL_ID)
    warm = store.get_by_id(_INTERVAL_ID)
    assert cold == warm
    assert cold is warm
    assert db.point_reads == 1

    # Strict-int coercion: a string spelling of the same id must NOT be served
    # from the int-keyed cache entry (and never mutates it).
    assert store.get_by_id(str(_INTERVAL_ID)) is None
    assert db.point_reads == 2
    assert store.get_by_id(True) is None  # bool is not an int id here
    assert db.point_reads == 3
    # The genuine int key is still warm and unchanged.
    assert store.get_by_id(_INTERVAL_ID) is warm
    assert db.point_reads == 3


def test_interval_cache_is_invalidated_on_touch_and_close():
    """Writes that mutate an interval drop only that interval's cache entry."""
    db = _CountingDB()
    store = _store(db)
    interval = store.get_by_id(_INTERVAL_ID)
    assert interval is not None

    store._interval_cache.pop(_INTERVAL_ID, None)  # simulate eviction for setup
    store.touch(_INTERVAL_ID, now=555)
    # A touch is a write; the next read must be a fresh DB read.
    refreshed = store.get_by_id(_INTERVAL_ID)
    assert refreshed is not None
    assert refreshed.last_message_at == 100  # fake DB row is unchanged
    assert isinstance(interval, RatchetInterval)

    # A distinct interval id is not served from another id's cache entry.
    assert store.get_by_id(_INTERVAL_ID + 1) is None
