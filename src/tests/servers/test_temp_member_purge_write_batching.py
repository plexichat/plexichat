"""Write-count regression for temporary-membership purge.

``MemberCrudMixin.purge_temporary_members`` used to issue one ``DELETE`` on
``srv_member_roles`` and one on ``srv_members`` per revoked membership, so a
single disconnect from a user holding N temporary invites cost 2N statements
and 2N autocommit round trips. Those writes are now bounded set-based IN
batches: the number of statements is constant in N (subject to the 500-id
placeholder cap), the earned/revoked decision and the returned server ids are
unchanged, and every revoked member's cached channel verdicts are retired even
if a batch fails part-way (fail closed, no stale grants).
"""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from src.core.servers.handlers.members import crud as crud_module
from src.core.servers.handlers.members.crud import MemberCrudMixin

pytestmark = [pytest.mark.unit, pytest.mark.servers]

_BATCH = 500


class _FakeDB:
    """DB double: returns memberships/earned rows and records every write."""

    def __init__(self, n, earned=(), fail_on=None):
        self.members = [
            {"member_id": 1000 + i, "server_id": 7000 + i} for i in range(n)
        ]
        self.earned = set(earned)
        self.fail_on = fail_on
        self.executes = []  # [(normalized_sql, params_tuple), ...]

    def fetch_all(self, query, params=None):
        if "is_temporary = 1" in query:
            return [dict(row) for row in self.members]
        if "srv_member_roles" in query:
            return [{"member_id": mid} for mid in tuple(params) if mid in self.earned]
        return []

    def execute(self, query, params=None):
        sql = " ".join(query.split())
        params = tuple(params or ())
        self.executes.append((sql, params))
        if self.fail_on is not None and self.fail_on(sql):
            raise RuntimeError("injected batched-write failure")


class _Manager:
    def __init__(self):
        self.invalidations = []
        self.audits = []

    def _buffered_audit(self):
        return nullcontext()

    def _invalidate_member_caches(self, server_id, user_id):
        self.invalidations.append((server_id, user_id))

    def _log_audit(self, *args, **kwargs):
        self.audits.append((args, kwargs))


def _purge(n, earned=(), fail_on=None, swallow=False):
    db = _FakeDB(n, earned, fail_on)
    manager = _Manager()
    mixin = MemberCrudMixin()
    mixin.db = db
    mixin.manager = manager
    orig = crud_module.logger
    crud_module.logger = type("L", (), {"info": staticmethod(lambda *a, **k: None)})()
    revoked = []
    try:
        try:
            revoked = mixin.purge_temporary_members(42)
        except RuntimeError:
            if not swallow:
                raise
    finally:
        crud_module.logger = orig
    return db, manager, revoked


def _role_deletes(db):
    return [
        p for sql, p in db.executes if sql.startswith("DELETE FROM srv_member_roles")
    ]


def _member_deletes(db):
    return [p for sql, p in db.executes if sql.startswith("DELETE FROM srv_members")]


def test_write_count_constant_in_number_of_memberships():
    """Before: 2N DELETEs. After: one role DELETE + one member DELETE."""
    db_small, _, small = _purge(2)
    db_large, _, large = _purge(50)

    assert len(_role_deletes(db_small)) == 1
    assert len(_member_deletes(db_small)) == 1
    assert len(_role_deletes(db_large)) == 1
    assert len(_member_deletes(db_large)) == 1
    assert len(small) == 2
    assert len(large) == 50
    # One batch covers every revoked membership id, in row order.
    assert _role_deletes(db_large) == [tuple(1000 + i for i in range(50))]
    assert _member_deletes(db_large) == [tuple(1000 + i for i in range(50))]


def test_write_batches_are_bounded():
    """More memberships than one batch -> a bounded number of DELETEs."""
    db, _, revoked = _purge(_BATCH + 3)

    assert [len(p) for p in _role_deletes(db)] == [_BATCH, 3]
    assert [len(p) for p in _member_deletes(db)] == [_BATCH, 3]
    assert len(revoked) == _BATCH + 3


def test_earned_member_kept_and_revoked_ids_unchanged():
    """Batching preserves the exact semantics and payload ordering."""
    db, manager, revoked = _purge(3, earned={1001})

    # 1001 earned a non-default role: flag cleared, never deleted.
    updates = [p for sql, p in db.executes if sql.startswith("UPDATE srv_members")]
    assert updates == [(1001,)]
    deleted_ids = [
        mid for params in _role_deletes(db) + _member_deletes(db) for mid in params
    ]
    assert 1001 not in deleted_ids
    assert 1000 in deleted_ids and 1002 in deleted_ids
    assert revoked == [7000, 7002]
    # Cache/audit side effects are still once per revoked member, in order.
    assert manager.invalidations == [(7000, 42), (7002, 42)]
    assert len(manager.audits) == 2


def test_partial_write_failure_still_invalidates_every_revoked_member():
    """Fail closed: a mid-batch failure cannot leave stale channel grants."""

    def fail_role_delete(sql):
        return sql.startswith("DELETE FROM srv_member_roles")

    db, manager, revoked = _purge(4, fail_on=fail_role_delete, swallow=True)

    assert revoked == []  # exception propagated; no success was reported
    # Even though the very first write failed, every revoked member's cached
    # decisions were retired -- no removed role survives in cache.
    assert manager.invalidations == [(7000 + i, 42) for i in range(4)]
    assert db.executes  # the failing write was attempted
