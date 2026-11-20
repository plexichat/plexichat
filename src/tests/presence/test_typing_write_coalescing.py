"""Regression tests for bounded coalescing of repeated typing DB writes.

A typing burst re-sends TYPING_START for the same (user, channel) many times in
one second. Before coalescing every frame issued two DB writes (a global expiry
DELETE plus a row UPSERT). These tests pin the new behaviour: one write per
short window, a hard bound on retained claims, and — critically — no loss of
correctness on stop/clear, Valkey outages, or window boundaries.
"""

import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest

sys.path.insert(0, "/root/code-plexichat/plexichat/src/utils")

from src.core.presence.manager import PresenceManager
from src.core.presence.manager.typing import _typing_write_coalescer
from src.core.presence.typing_coalesce import TypingWriteCoalescer

USER = 555_000_111
CHANNEL = 555_000_222
TIMEOUT_MS = 6000


class CountingDB:
    def __init__(self):
        self.executes = 0
        self.upserts = 0
        self.typing_rows = {}

    @property
    def writes(self):
        return self.executes + self.upserts

    def execute(self, sql, params=()):
        self.executes += 1
        if "DELETE FROM pres_typing" in sql and "channel_id" in sql:
            self.typing_rows.pop((params[0], params[1]), None)
        elif "DELETE FROM pres_typing" in sql and "user_id" in sql:
            for k in [k for k in self.typing_rows if k[0] == params[0]]:
                self.typing_rows.pop(k, None)

    def upsert(self, table, columns, values, key_cols, update_cols):
        assert table == "pres_typing"
        self.upserts += 1
        uid, cid, started, expires = values[1], values[2], values[3], values[4]
        self.typing_rows[(uid, cid)] = (started, expires)

    def fetch_all(self, sql, params=()):
        if "SELECT channel_id FROM pres_typing" in sql:
            return [
                {"channel_id": cid}
                for (uid, cid) in self.typing_rows
                if uid == params[0]
            ]
        return []


class FakeValkey:
    def __init__(self, fail=False):
        self.fail = fail
        self.sets = {}
        self.expire_calls = 0

    def sadd(self, key, val):
        if self.fail:
            raise RuntimeError("valkey down")
        self.sets.setdefault(key, set()).add(val)

    def srem(self, key, val):
        if self.fail:
            raise RuntimeError("valkey down")
        if key in self.sets:
            self.sets[key].discard(val)

    def smembers(self, key):
        return set(self.sets.get(key, ()))

    def delete(self, key):
        self.sets.pop(key, None)

    def expire(self, key, ttl):
        self.expire_calls += 1


@pytest.fixture(autouse=True)
def clean_coalescer():
    _typing_write_coalescer.clear()
    yield
    _typing_write_coalescer.clear()


@contextmanager
def _manager(db, valkey):
    pm = PresenceManager(db)
    now = [1_700_000_000_000]

    def fake_ts(_self):
        now[0] += 1
        return now[0]

    patches = [
        patch.object(PresenceManager, "_get_timestamp", fake_ts),
        patch.object(PresenceManager, "_validate_user", lambda self, uid: None),
        patch.object(PresenceManager, "_generate_id", lambda self: 1),
        patch(
            "src.core.presence.manager.typing.valkey_available",
            return_value=not valkey.fail,
        ),
        patch(
            "src.core.presence.manager.typing.get_valkey_client",
            return_value=valkey,
        ),
    ]
    for p in patches:
        p.start()
    yield pm, now
    for p in reversed(patches):
        p.stop()


def _burst(pm, count, uid=USER, cid=CHANNEL):
    return [pm.start_typing(uid, cid) for _ in range(count)]


# ---------------------------------------------------------------------------
# Coalescing + bounding
# ---------------------------------------------------------------------------


def test_burst_collapses_to_one_db_write_window():
    db, valkey = CountingDB(), FakeValkey()
    with _manager(db, valkey) as (pm, _now):
        indicators = _burst(pm, 100)

    assert db.executes == 1  # one global expiry DELETE
    assert db.upserts == 1  # one row UPSERT
    assert db.writes == 2
    # Live state is still refreshed on every frame, including coalesced ones.
    assert valkey.expire_calls == 200
    # All coalesced frames report the actually persisted timestamps.
    assert all(i.started_at == indicators[0].started_at for i in indicators)
    assert all(i.expires_at == indicators[0].expires_at for i in indicators)
    assert indicators[0].expires_at - indicators[0].started_at == TIMEOUT_MS
    assert _typing_write_coalescer.stats()["coalesced"] == 99


def test_coalescing_is_per_channel():
    db, valkey = CountingDB(), FakeValkey()
    with _manager(db, valkey) as (pm, _now):
        _burst(pm, 10, cid=111)
        _burst(pm, 10, cid=222)

    assert db.upserts == 2
    assert _typing_write_coalescer.stats()["size"] == 2


def test_tracker_is_bounded_under_distinct_channel_flood():
    tracker = TypingWriteCoalescer(window_seconds=60, max_entries=8)
    for i in range(500):
        tracker.claim((USER, i))
    assert tracker.stats()["size"] == 8


def test_claim_expires_after_window_with_exact_pruning():
    now = [0.0]
    tracker = TypingWriteCoalescer(window_seconds=1.0, clock=lambda: now[0])
    assert tracker.claim((USER, CHANNEL)).write is True
    now[0] = 0.9
    assert tracker.claim((USER, CHANNEL)).write is False
    now[0] = 1.0
    assert tracker.claim((USER, CHANNEL)).write is True
    assert tracker.stats()["size"] == 1


# ---------------------------------------------------------------------------
# Correctness guards
# ---------------------------------------------------------------------------


def test_stop_invalidates_claim_so_restart_writes_again():
    db, valkey = CountingDB(), FakeValkey()
    with _manager(db, valkey) as (pm, _now):
        pm.start_typing(USER, CHANNEL)
        pm.stop_typing(USER, CHANNEL)
        assert (USER, CHANNEL) not in db.typing_rows
        # Same (user, channel) inside the window: must re-insert the row.
        pm.start_typing(USER, CHANNEL)

    assert db.upserts == 2


def test_clear_all_typing_invalidates_user_claims():
    db, valkey = CountingDB(), FakeValkey()
    with _manager(db, valkey) as (pm, _now):
        pm.start_typing(USER, 111)
        pm.start_typing(USER, 222)
        assert pm.clear_all_typing(USER) == [111, 222]
        pm.start_typing(USER, 111)
        pm.start_typing(USER, 222)

    assert db.upserts == 4
    assert _typing_write_coalescer.stats()["size"] == 2


def test_no_valkey_never_coalesces():
    """Without Valkey the DB row is the only source of truth: write always."""
    db, valkey = CountingDB(), FakeValkey(fail=True)
    with _manager(db, valkey) as (pm, _now):
        indicators = _burst(pm, 25)

    assert db.upserts == 25
    assert db.executes == 25
    assert _typing_write_coalescer.stats()["writes"] == 25
    assert _typing_write_coalescer.stats()["coalesced"] == 0
    # Each call must refresh its own expiry in the fallback path.
    assert indicators[-1].expires_at > indicators[0].expires_at


def test_valkey_failure_mid_burst_falls_back_to_full_writes():
    db, valkey = CountingDB(), FakeValkey()
    with _manager(db, valkey) as (pm, _now):
        _burst(pm, 5)  # warm claim, coalesced
        valkey.fail = True
        _burst(pm, 5)  # Valkey now failing: every frame must write

    assert db.upserts == 6
