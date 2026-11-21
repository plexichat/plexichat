"""Query-count regression for temporary-membership purge role checks.

``MemberCrudMixin.purge_temporary_members`` used to ask "did this member earn
a non-default role?" once per temporary membership, issuing one
``srv_member_roles``/``srv_roles`` join per row (N+1). A disconnect can revoke
many temporary memberships at once, so the cost grew linearly with the number
of memberships. The probe is now a bounded set-based IN batch, keeping the
query count constant while preserving the exact authorization semantics.

These tests use a deterministic fake DB that counts SELECTs (a wall-clock
assert would be flaky on a small local DB), mirroring the style of
``test_query_count_batching.py``.
"""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from src.core.servers.handlers.members import crud as crud_module
from src.core.servers.handlers.members.crud import MemberCrudMixin

pytestmark = [pytest.mark.unit, pytest.mark.servers]

_BATCH = 500


class _FakeDB:
    """Minimal DB double: returns memberships and earned-role rows, counts reads."""

    def __init__(self, n, earned_member_ids=()):
        self.members = [
            {"member_id": 1000 + i, "server_id": 7000 + i} for i in range(n)
        ]
        self.earned_member_ids = set(earned_member_ids)
        self.selects = 0
        self.role_probes = []
        self.updates = []
        self.deletes = []

    def fetch_all(self, query, params=None):
        self.selects += 1
        if "is_temporary = 1" in query:
            return [dict(row) for row in self.members]
        if "srv_member_roles" in query:
            ids = tuple(params)
            self.role_probes.append(ids)
            return [{"member_id": mid} for mid in ids if mid in self.earned_member_ids]
        return []

    def fetch_one(self, query, params=None):
        # The batched implementation must never fall back to a per-member read.
        raise AssertionError("per-member fetch_one must not be used by the purge")

    def execute(self, query, params=None):
        if query.lstrip().upper().startswith("UPDATE"):
            self.updates.append(tuple(params))
        else:
            self.deletes.append((query, tuple(params)))
        return None


class _Manager:
    def __init__(self, db):
        self._db = db
        self.invalidations = []

    def _buffered_audit(self):
        return nullcontext()

    def _invalidate_member_caches(self, server_id, user_id):
        self.invalidations.append((server_id, user_id))

    def _log_audit(self, *args, **kwargs):
        pass


def _purge(n, earned_member_ids=()):
    db = _FakeDB(n, earned_member_ids)
    manager = _Manager(db)
    mixin = MemberCrudMixin()
    mixin.db = db
    mixin.manager = manager
    # Keep the real method under test; only silence the informational logger.
    orig = crud_module.logger
    crud_module.logger = type("L", (), {"info": staticmethod(lambda *a, **k: None)})()
    try:
        revoked = mixin.purge_temporary_members(42)
    finally:
        crud_module.logger = orig
    return db, manager, revoked


def test_query_count_is_constant_in_number_of_temporary_memberships():
    """Before: N+1 SELECTs. After: one membership read + one role batch."""
    db_small, _, small = _purge(2)
    db_large, _, large = _purge(50)

    assert db_small.selects == 2
    assert db_large.selects == 2
    assert len(small) == 2
    assert len(large) == 50
    # Exactly one bounded role probe, covering every membership id.
    assert db_large.role_probes == [tuple(1000 + i for i in range(50))]


def test_role_probe_is_chunked_to_bound_parameter_count():
    """More memberships than one batch -> a bounded number of IN batches."""
    db, _, revoked = _purge(_BATCH + 3)

    assert db.selects == 3  # membership read + two role batches
    assert [len(ids) for ids in db.role_probes] == [_BATCH, 3]
    assert len(revoked) == _BATCH + 3


def test_earned_member_kept_and_unearned_revoked():
    """Batching preserves the exact earned-role semantics (fail closed)."""
    db, manager, revoked = _purge(3, earned_member_ids={1001})

    # 1001 earned a role: flag cleared, not deleted, not reported revoked.
    assert db.updates == [(1001,)]
    # Deletes are now bounded IN batches, so collect ids from every batch
    # rather than assuming one id (one statement) per member.
    deleted_member_ids = [mid for _, params in db.deletes for mid in params]
    assert 1001 not in deleted_member_ids
    # 1000 and 1002 had no non-default role: both rows deleted and reported.
    assert 1000 in deleted_member_ids
    assert 1002 in deleted_member_ids
    assert revoked == [7000, 7002]
    assert manager.invalidations == [(7000, 42), (7002, 42)]
