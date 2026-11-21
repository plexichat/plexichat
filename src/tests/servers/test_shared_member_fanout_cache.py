"""Regression + measurement tests for the bounded shared-member cache.

``MemberOpsMixin.get_all_shared_member_ids`` answers the self-join fan-out that
resolves the full set of users sharing a server with a subject.  It backs
presence broadcasts, the presence-read authorization gate, disconnect typing
cleanup and profile visibility.  The audience only changes on a membership
write, yet every poll / burst re-issued the join.  The lookup is now memoized in
a bounded, short-TTL cache invalidated by the shared membership funnel.

These tests pin five properties:

1. a burst performs one authoritative query, not one per call (before/after);
2. different subjects never share an entry (no cross-user audience leak);
3. a membership write invalidates the audience immediately;
4. the cached list is copy-safe, bounded and TTL-bounded;
5. a corrupt entry or a failed authoritative lookup fails closed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.servers.manager import member_count_cache as mcc
from src.core.servers.manager import shared_member_cache as smc
from src.core.servers.manager.member_ops import MemberOpsMixin

pytestmark = [pytest.mark.unit]

SUBJECT = 9001
OTHER_SUBJECT = 9002


@pytest.fixture(autouse=True)
def _clean_cache():
    smc.invalidate_shared_member_cache()
    yield
    smc.invalidate_shared_member_cache()


class _FakeDB:
    """Minimal ``fetch_all`` stand-in that counts authoritative reads."""

    def __init__(self, shared):
        self.shared = {int(k): list(v) for k, v in shared.items()}
        self.calls = 0
        self.raise_error = False

    def fetch_all(self, query, params):
        self.calls += 1
        assert "srv_members" in query and "JOIN" in query
        if self.raise_error:
            raise RuntimeError("srv_members unavailable")
        subject = int(params[0])
        return [
            {"user_id": uid} for uid in self.shared.get(subject, []) if uid != subject
        ]


def _call(db, subject=SUBJECT):
    """Invoke the real mixin method against a fake ``self``."""
    return MemberOpsMixin.get_all_shared_member_ids(SimpleNamespace(_db=db), subject)


def _uncached_call(db, subject=SUBJECT):
    """The exact pre-optimization query, used only for the before measurement."""
    rows = db.fetch_all(
        """SELECT DISTINCT m2.user_id
           FROM srv_members m1
           JOIN srv_members m2 ON m1.server_id = m2.server_id
           WHERE m1.user_id = ? AND m2.user_id != ?""",
        (subject, subject),
    )
    return [row["user_id"] for row in rows]


def test_burst_reuses_shared_member_list():
    db = _FakeDB({SUBJECT: [1, 2, 3]})
    before = smc.shared_member_cache_stats()

    for _ in range(50):
        assert _call(db) == [1, 2, 3]

    assert db.calls == 1
    stats = smc.shared_member_cache_stats()
    assert stats["hits"] - before["hits"] == 49
    assert stats["misses"] - before["misses"] == 1


def test_perf_before_and_after_call_counts():
    """A 200-event presence burst: 200 joins -> 1, for one subject."""
    events, members = 200, 500
    audience = list(range(1, members + 1))

    # "before" is the original uncached path: one self-join per resolution.
    before_db = _FakeDB({SUBJECT: audience})
    for _ in range(events):
        assert _uncached_call(before_db) == audience
    assert before_db.calls == events

    # "after" is the cached method under test.
    smc.invalidate_shared_member_cache()
    after_db = _FakeDB({SUBJECT: audience})
    for _ in range(events):
        assert _call(after_db) == audience
    assert after_db.calls == 1


def test_different_subjects_never_share_an_entry():
    """No cross-user audience leak: distinct subjects get distinct sets."""
    db = _FakeDB({SUBJECT: [1, 2, 3], OTHER_SUBJECT: [7, 8]})
    before = smc.shared_member_cache_stats()

    assert _call(db, SUBJECT) == [1, 2, 3]
    assert _call(db, OTHER_SUBJECT) == [7, 8]
    assert _call(db, SUBJECT) == [1, 2, 3]
    # One authoritative read per distinct subject; audiences are never conflated.
    assert db.calls == 2

    stats = smc.shared_member_cache_stats()
    assert (stats["hits"] - before["hits"], stats["misses"] - before["misses"]) == (
        1,
        2,
    )


def test_membership_write_invalidates_immediately():
    db = _FakeDB({SUBJECT: [1, 2, 3]})
    assert _call(db) == [1, 2, 3]

    # Simulate a kick: the fake source changes and the shared membership funnel
    # drops the cached audience, exactly as join/leave/kick/ban do.
    db.shared[SUBJECT] = [1, 3]
    mcc.invalidate_member_count_cache()

    assert _call(db) == [1, 3]
    assert db.calls == 2


def test_cached_list_is_copy_safe():
    db = _FakeDB({SUBJECT: [1, 2, 3]})
    first = _call(db)
    first.append(999)
    first.sort()

    assert _call(db) == [1, 2, 3]
    assert db.calls == 1


def test_expired_entry_is_not_served():
    db = _FakeDB({SUBJECT: [1, 2, 3]})
    assert _call(db) == [1, 2, 3]

    key = SUBJECT
    with smc._shared_member_cache._lock:
        _exp, value, size = smc._shared_member_cache._data[key]
        smc._shared_member_cache._data[key] = (0.0, value, size)

    db.shared[SUBJECT] = [4]
    assert _call(db) == [4]
    assert db.calls == 2


def test_corrupt_cache_entry_fails_closed():
    """A malformed cached value must never widen or invent an audience."""
    db = _FakeDB({SUBJECT: [1, 2]})
    smc.prime_shared_member_ids(SUBJECT, [1, 2])
    with smc._shared_member_cache._lock:
        _exp, _value, size = smc._shared_member_cache._data[SUBJECT]
        smc._shared_member_cache._data[SUBJECT] = (_exp, "not-a-list", size)

    assert _call(db) == [1, 2]
    assert db.calls == 1


def test_authoritative_error_fails_closed_and_is_not_cached():
    """A failed lookup propagates (so callers deny) and leaves no warm entry."""
    db = _FakeDB({SUBJECT: [1, 2]})
    db.raise_error = True

    with pytest.raises(RuntimeError):
        _call(db)
    assert smc.shared_member_cache_stats()["size"] == 0

    # A subsequent successful read resolves from source and caches normally.
    db.raise_error = False
    assert _call(db) == [1, 2]
    assert _call(db) == [1, 2]
    assert db.calls == 2


def test_cache_is_bounded():
    """A flood of distinct subjects cannot grow the cache without limit."""
    capped = smc.BoundedLRU(
        max_entries=2,
        max_bytes=smc.SHARED_MEMBER_MAX_BYTES,
        ttl_seconds=100,
        size_of=smc._id_list_size,
    )
    for uid in range(5):
        capped.set(uid, [uid])
    assert capped.stats()["size"] == 2
