"""Regression + measurement tests for the bounded server member-id cache.

``MemberOpsMixin.get_member_user_ids`` answers the raw
``SELECT user_id FROM srv_members WHERE server_id = ?`` fan-out primitive.  It
is called once per gateway event, presence/typing update, voice listing and
REST enrichment -- all for the same server -- even though the roster only
changes on a membership write.  The lookup is now memoized in a bounded,
short-TTL cache that is invalidated by the shared membership funnel.

These tests pin five properties:

1. a burst performs one authoritative query, not one per call (before/after);
2. different servers and exclude filters never share an entry (no cross-audience
   leak, no cross-user leak);
3. a membership write invalidates the roster immediately;
4. cached lists are copy-safe and TTL-bounded;
5. a broken cache entry never widens the recipient set (fail closed).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.servers.manager import member_count_cache as mcc
from src.core.servers.manager import member_id_cache as midc
from src.core.servers.manager.member_ops import MemberOpsMixin

pytestmark = [pytest.mark.unit]

SERVER = 700
OTHER_SERVER = 701
EXCLUDED = 42


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    # The burst assertions count authoritative reads, not elapsed time. Pin the
    # entry TTL for the duration so a heavily loaded worker cannot let an entry
    # expire mid-burst and turn a caching regression test into a wall-clock
    # one; the TTL bound itself is covered by test_expired_entry_is_not_served,
    # which rewrites the entry's own expiry.
    monkeypatch.setattr(midc._member_id_cache, "_ttl", 3600.0)
    midc.invalidate_member_id_cache()
    yield
    midc.invalidate_member_id_cache()


class _FakeDB:
    """Minimal ``fetch_all`` stand-in that counts authoritative reads."""

    def __init__(self, rosters):
        self.rosters = dict(rosters)
        self.calls = 0

    def fetch_all(self, query, params=None):
        self.calls += 1
        assert "srv_members" in query
        sid = params[0]
        ids = list(self.rosters.get(sid, []))
        if len(params) > 1:
            ids = [uid for uid in ids if uid != params[1]]
        return [{"user_id": uid} for uid in ids]


def _call(db, server_id=SERVER, exclude_user_id=None):
    """Invoke the real mixin method against a fake ``self``."""
    return MemberOpsMixin.get_member_user_ids(
        SimpleNamespace(_db=db), server_id, exclude_user_id
    )


def test_burst_reuses_member_list():
    db = _FakeDB({SERVER: [1, 2, 3]})

    for _ in range(50):
        assert _call(db) == [1, 2, 3]

    assert db.calls == 1
    stats = midc.member_id_cache_stats()
    assert stats["hits"] == 49
    assert stats["misses"] == 1


def test_perf_before_and_after_call_counts():
    """A 100-event burst on a 500-member server: 100 queries -> 1."""
    events, members = 100, 500
    roster = list(range(1, members + 1))

    # "before" is the original uncached path: one query per call.
    before_db = _FakeDB({SERVER: roster})
    for _ in range(events):
        # Emulate the pre-cache body without touching the cached method.
        assert _uncached_call(before_db) == roster
    assert before_db.calls == events

    # "after" is the cached method under test.
    midc.invalidate_member_id_cache()
    after_db = _FakeDB({SERVER: roster})
    for _ in range(events):
        assert _call(after_db) == roster
    assert after_db.calls == 1


def _uncached_call(db, server_id=SERVER, exclude_user_id=None):
    """The exact pre-optimization query, used only for the before measurement."""
    query = "SELECT user_id FROM srv_members WHERE server_id = ?"
    params = [server_id]
    if exclude_user_id:
        query += " AND user_id != ?"
        params.append(exclude_user_id)
    rows = db.fetch_all(query, tuple(params))
    return [row["user_id"] for row in rows]


def test_different_servers_and_exclusions_never_share_an_entry():
    db = _FakeDB(
        {
            SERVER: [1, 2, 3],
            OTHER_SERVER: [9, 8],
        }
    )

    assert _call(db, SERVER) == [1, 2, 3]
    assert _call(db, OTHER_SERVER) == [9, 8]
    assert _call(db, SERVER, exclude_user_id=1) == [2, 3]
    assert _call(db, SERVER, exclude_user_id=2) == [1, 3]
    # One authoritative read per distinct key -- a server's roster and its
    # filtered variants are never conflated.
    assert db.calls == 4

    # Re-peating a key is a pure hit.
    assert _call(db, SERVER, exclude_user_id=1) == [2, 3]
    assert db.calls == 4


def test_membership_write_invalidates_immediately():
    db = _FakeDB({SERVER: [1, 2, 3]})
    assert _call(db) == [1, 2, 3]

    # Simulate a kick: the fake source changes and the shared membership funnel
    # drops the cached roster, exactly as join/leave/kick/ban do.
    db.rosters[SERVER] = [1, 3]
    mcc.invalidate_member_count_cache()

    assert _call(db) == [1, 3]
    assert db.calls == 2


def test_cached_list_is_copy_safe():
    db = _FakeDB({SERVER: [1, 2, 3]})
    first = _call(db)
    first.append(999)
    first.sort()

    assert _call(db) == [1, 2, 3]
    assert db.calls == 1


def test_expired_entry_is_not_served():
    db = _FakeDB({SERVER: [1, 2, 3]})
    assert _call(db) == [1, 2, 3]

    key = (SERVER, None)
    with midc._member_id_cache._lock:
        _exp, value, size = midc._member_id_cache._data[key]
        midc._member_id_cache._data[key] = (0.0, value, size)

    db.rosters[SERVER] = [4]
    assert _call(db) == [4]
    assert db.calls == 2


def test_corrupt_cache_entry_fails_closed():
    """A malformed cached value must never widen or invent an audience."""
    db = _FakeDB({SERVER: [1, 2]})
    midc.prime_member_user_ids(SERVER, None, [1, 2])
    key = (SERVER, None)
    with midc._member_id_cache._lock:
        _exp, _value, size = midc._member_id_cache._data[key]
        midc._member_id_cache._data[key] = (_exp, "not-a-list", size)

    assert _call(db) == [1, 2]
    assert db.calls == 1
