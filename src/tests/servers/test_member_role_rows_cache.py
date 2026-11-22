"""Regression + measurement tests for the member-role-rows L0 cache.

``CacheOpsMixin._get_member_role_rows`` used to issue a fresh
``srv_roles`` / ``srv_member_roles`` join on *every* authorization check. It is
a hot primitive: permission resolution, role-hierarchy guards, bans, voice
moderation and automod call it repeatedly while hydrating one request. The
result is now memoized in a bounded LRU keyed by the full identity
``(server, user, RBAC epoch)``.

These tests pin four properties:

1. the repeated-join work drops on warm reads (measured as issued SELECTs);
2. entries are pair-scoped and cannot leak one member's roles to another;
3. an RBAC epoch bump (the invalidation every role/membership write already
   performs) retires the cached rows, so a revoked role cannot survive;
4. the cache is hard-bounded and any cache error degrades to the authoritative
   database -- it never synthesizes or widens an authorization decision.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.bounded_lru import BoundedLRU
from src.core.servers.channel_access_cache import get_channel_access_cache
from src.core.servers.manager import member_role_rows_cache as mrc
from src.core.servers.manager.cache_ops import CacheOpsMixin

pytestmark = [pytest.mark.unit, pytest.mark.servers]


@pytest.fixture(autouse=True)
def _clean_role_rows_cache():
    """Isolate the module-level cache and the shared RBAC epochs per test."""
    access = get_channel_access_cache()
    with access._lock:
        access._server_epochs.clear()
        access._user_epochs.clear()
    mrc.invalidate_member_role_rows_cache()
    yield
    mrc.invalidate_member_role_rows_cache()
    with access._lock:
        access._server_epochs.clear()
        access._user_epochs.clear()


class _RoleDB:
    """Count role joins and serve configurable authoritative rows."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.calls = []

    def fetch_all(self, query, params=None):
        assert "srv_member_roles" in query
        uid = tuple(params)[0]
        self.calls.append(uid)
        return [dict(row) for row in self.rows.get(uid, ())]


class _Manager(CacheOpsMixin):
    """Minimal concrete manager: only ``get_member`` is needed by this path."""

    def __init__(self, db, members):
        self._db = db
        self._members = members

    def get_member(self, server_id, user_id):
        return self._members.get((int(server_id), int(user_id)))


def _manager(rows=None, members=None):
    db = _RoleDB(rows)
    members = members or {}
    return _Manager(db, members), db


def _member(member_id):
    return SimpleNamespace(id=member_id)


def test_repeated_resolution_hits_db_once():
    """Before: 50 joins. After: one join, then 49 cache hits."""
    manager, db = _manager(
        rows={11: [{"id": 1, "name": "mod", "position": 5}]},
        members={(1, 10): _member(11)},
    )

    # The LRU is process-global and its hit counter is deliberately cumulative
    # across clears (BoundedLRU.clear preserves counters), so earlier tests in
    # a single-process (-n 0) run may already have recorded hits. Measure the
    # delta attributable to this burst rather than an absolute value.
    hits_before = mrc.member_role_rows_cache_stats()["hits"]

    for _ in range(50):
        assert manager._get_member_role_rows(1, 10) == [
            {"id": 1, "name": "mod", "position": 5}
        ]

    # Naive per-call resolution would have issued 50 SELECTs; the cache issues
    # exactly one for the whole burst.
    assert len(db.calls) == 1
    assert db.calls == [11]
    # 50 resolutions, one miss -> exactly 49 served from the cache.
    assert mrc.member_role_rows_cache_stats()["hits"] - hits_before == 49


def test_rows_are_server_and_user_scoped_and_negative_cached():
    """No cross-user leak; 'no roles' is a cached, authoritative answer."""
    manager, db = _manager(
        rows={
            11: [{"id": 1, "name": "alice-role"}],
            22: [{"id": 2, "name": "bob-role"}],
        },
        members={
            (1, 10): _member(11),
            (1, 20): _member(22),
            (2, 10): _member(33),  # same user id, different server -> miss
            # user 99 is not a member -> cached empty result
        },
    )

    assert manager._get_member_role_rows(1, 10) == [{"id": 1, "name": "alice-role"}]
    assert manager._get_member_role_rows(1, 20) == [{"id": 2, "name": "bob-role"}]
    # Same user id in a different server is a distinct key and a distinct join.
    assert manager._get_member_role_rows(2, 10) == []
    assert db.calls == [11, 22, 33]

    # Repeating either call is a hit -- one member's rows are never served for
    # another, and neither is re-read from the database.
    assert manager._get_member_role_rows(1, 10) == [{"id": 1, "name": "alice-role"}]
    assert manager._get_member_role_rows(1, 20) == [{"id": 2, "name": "bob-role"}]
    assert manager._get_member_role_rows(2, 10) == []
    assert db.calls == [11, 22, 33]

    # A missing member resolves to an empty list, which is cached too.
    assert manager._get_member_role_rows(1, 99) == []
    assert manager._get_member_role_rows(1, 99) == []
    assert len(db.calls) == 3  # no join at all for a non-member


def test_rows_are_copied_in_and_out():
    """A caller mutating its rows cannot poison a later authz decision."""
    source = [{"id": 1, "name": "mod", "position": 5}]
    manager, db = _manager(
        rows={11: source}, members={(1, 10): _member(11)}
    )

    first = manager._get_member_role_rows(1, 10)
    first[0]["name"] = "tampered"
    first.append({"id": 999, "name": "injected"})

    second = manager._get_member_role_rows(1, 10)
    assert second == [{"id": 1, "name": "mod", "position": 5}]
    assert len(db.calls) == 1


def test_role_epoch_bump_revokes_cached_rows_fail_closed():
    """A role write bumps the epoch; the stale grant is unreachable at once."""
    manager, db = _manager(
        rows={11: [{"id": 1, "name": "mod"}]},
        members={(1, 10): _member(11)},
    )

    assert manager._get_member_role_rows(1, 10) == [{"id": 1, "name": "mod"}]
    assert len(db.calls) == 1

    # Every role assignment/removal already performs this bump. Changing the
    # stale cache alone must have no effect on the next authorization read.
    mrc.prime_member_role_rows(1, 10, [{"id": 777, "name": "ghost"}])
    get_channel_access_cache().bump(1, 10)
    db.rows[11] = [{"id": 2, "name": "revoked-and-replaced"}]

    assert manager._get_member_role_rows(1, 10) == [
        {"id": 2, "name": "revoked-and-replaced"}
    ]
    assert len(db.calls) == 2  # recomputed from the authoritative source


def test_server_epoch_bump_revokes_every_member_in_scope():
    """A server-wide role/permission change retires all cached role rows."""
    manager, db = _manager(
        rows={
            11: [{"id": 1}],
            22: [{"id": 2}],
        },
        members={(1, 10): _member(11), (1, 20): _member(22)},
    )
    manager._get_member_role_rows(1, 10)
    manager._get_member_role_rows(1, 20)
    assert len(db.calls) == 2

    get_channel_access_cache().bump(1)
    manager._get_member_role_rows(1, 10)
    manager._get_member_role_rows(1, 20)
    assert len(db.calls) == 4


def test_full_invalidation_drops_cached_rows():
    manager, db = _manager(
        rows={11: [{"id": 1}]}, members={(1, 10): _member(11)}
    )
    assert manager._get_member_role_rows(1, 10) == [{"id": 1}]
    assert len(db.calls) == 1

    mrc.invalidate_member_role_rows_cache()
    assert manager._get_member_role_rows(1, 10) == [{"id": 1}]
    assert len(db.calls) == 2


def test_cache_is_hard_bounded():
    cache = mrc._member_role_rows_cache
    assert isinstance(cache, BoundedLRU)
    stats = mrc.member_role_rows_cache_stats()
    assert stats["max_entries"] > 0
    assert stats["max_bytes"] > 0
    assert cache._ttl > 0

    # Churn far more distinct members than the ceiling: the cache must evict.
    for uid in range(mrc.MEMBER_ROLE_ROWS_MAX_ENTRIES * 2):
        mrc.prime_member_role_rows(1, 10_000 + uid, [{"id": uid}])

    stats = mrc.member_role_rows_cache_stats()
    assert stats["size"] <= stats["max_entries"]
    assert stats["bytes"] <= stats["max_bytes"]
    assert stats["evictions"] > 0


def test_cache_errors_fail_closed_to_the_database(monkeypatch):
    """A broken cache costs a query; it cannot change the authz answer."""
    manager, db = _manager(
        rows={11: [{"id": 1, "name": "mod"}]},
        members={(1, 10): _member(11)},
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("cache backend down")

    monkeypatch.setattr(mrc._member_role_rows_cache, "get", boom)
    monkeypatch.setattr(mrc._member_role_rows_cache, "set", boom)

    assert manager._get_member_role_rows(1, 10) == [{"id": 1, "name": "mod"}]
    assert manager._get_member_role_rows(1, 10) == [{"id": 1, "name": "mod"}]
    assert len(db.calls) == 2  # both reads resolved authoritatively
