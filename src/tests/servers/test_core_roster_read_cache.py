"""Core roster read: query reduction, parity, authz gate, credential isolation.

``ServersManager.get_members`` is one of the hottest member reads: presence,
gateway fan-out, websocket handlers and REST all page the same server roster
many times in one request.  Before this change its membership check lived
*inside* the cached loader, so a warm (or attacker-poisoned) page skipped the
authorization gate entirely, and roster mutations only cleared the unrelated
``server_members_api:*`` HTTP namespace -- never the core cache keys.

Four contracts are pinned here:

1. PARITY + QUERY REDUCTION -- a warm read is byte-for-byte identical to a
   cold one and does not re-issue the two heavy roster queries (3 DB calls
   cold -> 1 per warm read; the retained call is the deliberately-uncached
   authorization gate, which is intentionally never cached away).
2. NO CROSS-USER LEAK -- the key is partitioned by the requesting credential;
   a poisoned entry for one viewer is never served to another.
3. AUTHZ BEFORE CACHE -- a non-member is rejected on every call *before* any
   cache tier is consulted, even when an attacker pre-seeds their cache key.
4. INVALIDATION -- every roster-changing mutation reaches ``srv_roster:*``.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest

from src.core.database import invalidate_pattern
from src.core.database.cache import decorators as cache_decorators
from src.core.database.cache import operations as cache_operations
from src.core.database.cache.base import (
    mem_cache_clear_pattern,
    mem_cache_get,
    mem_cache_set,
)
from src.core.servers.exceptions import ServerAccessDeniedError
from src.core.servers.handlers.members import crud as member_crud
from src.core.servers.handlers.roles import assignments as role_assignments
from src.core.servers.manager.cache_ops import CacheOpsMixin
from src.core.servers.manager.member_ops import (
    MemberOpsMixin,
    _roster_cache_key,
    _ROSTER_CACHE_PREFIX,
)

SERVER_ID = 5


def _member_row(mid, uid, nickname="before"):
    return {
        "id": mid,
        "server_id": SERVER_ID,
        "user_id": uid,
        "nickname": nickname,
        "joined_at": 1,
        "updated_at": 1,
        "muted": 0,
        "deafened": 0,
        "inviter_id": None,
        "timeout_until": None,
        "timeout_reason": None,
        "is_temporary": 0,
    }


class _CountingDb:
    """Fake DB that records statements so the query reduction is measurable."""

    def __init__(self, member_rows, roles=None):
        self.member_rows = [dict(r) for r in member_rows]
        self.roles = dict(roles or {})
        self.queries = []

    def fetch_one(self, sql, params):
        self.queries.append(sql)
        if "srv_members" in sql and "SELECT 1" in sql:
            sid, uid = params
            return (
                {"1": 1}
                if any(
                    r["server_id"] == sid and r["user_id"] == uid
                    for r in self.member_rows
                )
                else None
            )
        return None

    def fetch_all(self, sql, params):
        self.queries.append(sql)
        if "srv_member_roles" in sql:
            wanted = set(params)
            return [
                {"member_id": mid, "role_id": rid}
                for mid, rids in self.roles.items()
                if mid in wanted
                for rid in rids
            ]
        if "srv_members" in sql:
            # Pagination is irrelevant to the cache boundary under test; hand
            # back the full (small) roster so counts stay deterministic.
            return [dict(r) for r in self.member_rows]
        return []

    @property
    def authz_queries(self):
        return [q for q in self.queries if "SELECT 1" in q]

    @property
    def roster_queries(self):
        return [
            q
            for q in self.queries
            if "srv_member_roles" in q or "SELECT * FROM srv_members" in q
        ]


class _Manager(MemberOpsMixin):
    """Minimal manager exposing only what the roster read touches."""

    def __init__(self, db):
        self._db = db


@pytest.fixture
def cached_env(monkeypatch):
    """Run the real @cached loader against the in-memory tier only."""
    monkeypatch.setattr(cache_decorators, "get_client", lambda: None)
    monkeypatch.setattr(cache_operations, "get_client", lambda: None)
    mem_cache_clear_pattern(f"{_ROSTER_CACHE_PREFIX}:*")
    yield
    mem_cache_clear_pattern(f"{_ROSTER_CACHE_PREFIX}:*")


def _ids(members):
    return [m.user_id for m in members]


def test_warm_read_matches_cold_and_eliminates_roster_queries(cached_env):
    """Warm page is identical and skips both heavy roster queries."""
    db = _CountingDb([_member_row(10, 101), _member_row(11, 102)], {10: [7]})
    mgr = _Manager(db)

    cold = mgr.get_members(101, SERVER_ID)
    assert len(db.roster_queries) == 2  # roster rows + role rows
    assert len(db.authz_queries) == 1  # membership gate
    assert _ids(cold) == [101, 102]
    assert [m.roles for m in cold] == [[7], []]

    warm = mgr.get_members(101, SERVER_ID)

    # Parity: identical values (including the enum-free role ids) on a hit.
    assert _ids(warm) == _ids(cold)
    assert [m.roles for m in warm] == [m.roles for m in cold]
    assert [m.nickname for m in warm] == [m.nickname for m in cold]

    # Query reduction: 3 DB calls on the cold read, 1 per warm read. The two
    # eliminated calls are the expensive SELECT * roster + role lookups.
    assert len(db.roster_queries) == 2  # 2 -> +0
    assert len(db.authz_queries) == 2  # gate still runs on every call: 1 -> +1
    assert len(db.queries) == 4  # 3 cold + 1 authz-only warm


def test_no_cross_user_leak(cached_env):
    """Two credentials get distinct keys; a poisoned key is never replayed."""
    db = _CountingDb([_member_row(10, 101), _member_row(11, 102)], {10: [7]})
    mgr = _Manager(db)

    alice = mgr.get_members(101, SERVER_ID)
    bob = mgr.get_members(102, SERVER_ID)
    assert _ids(alice) == [101, 102]
    assert _ids(bob) == [101, 102]

    alice_key = _roster_cache_key(None, 101, SERVER_ID)
    bob_key = _roster_cache_key(None, 102, SERVER_ID)
    assert alice_key != bob_key
    assert len(db.roster_queries) == 4  # one cold read per credential

    # Poison Alice's entry; Bob must never observe Alice's cached page.
    mem_cache_set(alice_key, [{"user_id": 999}], 60)
    bob_again = mgr.get_members(102, SERVER_ID)
    assert [m.user_id for m in bob_again] == [101, 102]
    assert len(db.roster_queries) == 4  # Bob's warm read reused his own entry


def test_authz_gate_runs_before_cache_even_with_poisoned_entry(cached_env):
    """A non-member is denied before any cache lookup, poison notwithstanding."""
    db = _CountingDb([_member_row(10, 101)])
    mgr = _Manager(db)

    # 999 is not a member. Pre-seed the key an attacker would try to replay.
    poison_key = _roster_cache_key(None, 999, SERVER_ID)
    poisoned = [{"user_id": 999, "nickname": "sneaky"}]
    mem_cache_set(poison_key, poisoned, 60)

    with pytest.raises(ServerAccessDeniedError):
        mgr.get_members(999, SERVER_ID)

    # Exactly one query: the membership gate. No roster query ran, and the
    # poisoned entry was neither served nor overwritten (fail closed).
    assert len(db.queries) == 1
    assert db.roster_queries == []
    assert mem_cache_get(poison_key) == poisoned


def test_denial_does_not_create_a_cache_entry(cached_env):
    """A rejected credential neither reads nor populates the roster cache."""
    db = _CountingDb([_member_row(10, 101)])
    mgr = _Manager(db)
    key = _roster_cache_key(None, 999, SERVER_ID)
    mem_cache_clear_pattern(key)

    with pytest.raises(ServerAccessDeniedError):
        mgr.get_members(999, SERVER_ID)

    assert mem_cache_get(key) is None
    assert db.roster_queries == []


def test_mutation_invalidation_refreshes_cached_roster(cached_env):
    """After a mutation drops the namespace the next read reflects new data."""
    db = _CountingDb([_member_row(10, 101, nickname="before")])
    mgr = _Manager(db)

    assert mgr.get_members(101, SERVER_ID)[0].nickname == "before"

    db.member_rows[0]["nickname"] = "after"
    # Still stale while warm: proves the page was actually cached.
    assert mgr.get_members(101, SERVER_ID)[0].nickname == "before"

    invalidate_pattern(f"{_ROSTER_CACHE_PREFIX}:*")
    assert mgr.get_members(101, SERVER_ID)[0].nickname == "after"


def test_all_roster_mutations_reach_the_invalidation_helper():
    """Every mutation that can change a cached page invalidates it."""
    # Field/role edits drop the roster namespace directly.
    for handler in (
        member_crud.MemberCrudMixin.update_member,
        role_assignments.RoleAssignmentMixin.assign_role,
        role_assignments.RoleAssignmentMixin.remove_role,
        role_assignments.RoleAssignmentMixin.assign_roles_from_whitelist,
    ):
        assert "_invalidate_roster_caches" in inspect.getsource(handler), (
            f"{handler.__qualname__} does not invalidate the cached roster"
        )
    # Membership lifecycle mutations funnel through the shared helper, which
    # is itself pinned to clear the roster namespace below.
    for handler in (
        member_crud.MemberCrudMixin.add_member,
        member_crud.MemberCrudMixin.kick_member,
        member_crud.MemberCrudMixin.purge_temporary_members,
    ):
        assert "_invalidate_member_caches" in inspect.getsource(handler), (
            f"{handler.__qualname__} does not invalidate member caches"
        )
    assert "_invalidate_roster_caches" in inspect.getsource(
        CacheOpsMixin._invalidate_member_caches
    )


def test_invalidation_glob_matches_credential_partitioned_keys(cached_env):
    """The mutation glob reaches every viewer's key for the changed server."""
    keys = [_roster_cache_key(None, viewer, SERVER_ID) for viewer in (101, 102, 999)]
    for key in keys:
        mem_cache_set(key, [{"poison": True}], 60)
        assert mem_cache_get(key) is not None

    ops = object.__new__(CacheOpsMixin)
    with patch("src.core.database.invalidate_pattern") as invalidate:
        ops._invalidate_roster_caches(SERVER_ID)
    invalidate.assert_called_once_with(f"{_ROSTER_CACHE_PREFIX}:*:{SERVER_ID}:*")

    # And the real call evicts every credential's page for that server.
    ops._invalidate_roster_caches(SERVER_ID)
    assert all(mem_cache_get(key) is None for key in keys)
