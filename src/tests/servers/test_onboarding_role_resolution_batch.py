"""Batched resolution of onboarding role-option whitelists.

Validating a ``SELECT_ROLES`` step used to call ``ServerManager.get_role``
once per whitelisted role. Each call issued a role read *and* a membership
read, so a full step cost 2N queries and grew linearly with the option count.

The replacement resolves the whole whitelist with bounded ``IN`` batches
(at most 500 bound ids) while every role still goes through the same
``can_manage_role`` hierarchy decision. These tests pin the query reduction,
the chunk bound, and the unchanged authorization behaviour.
"""

import json

import pytest

from src.core.servers.exceptions import OnboardingError, RoleNotFoundError
from src.core.servers.manager.onboarding_ops import (
    _ROLE_LOOKUP_CHUNK_SIZE,
    OnboardingManager,
    _resolve_whitelist_roles,
)
from src.core.servers.models import OnboardingStepType
from src.core.servers import permission_utils


class _FakeDB:
    """Minimal ``fetch_all`` double used for the chunking unit tests."""

    def __init__(self):
        self.calls = []

    def fetch_all(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return [
            {"id": rid, "server_id": params[-1], "position": rid} for rid in params[:-1]
        ]


class _ReadSpy:
    """Count every DB read while transparently delegating to the real DB."""

    def __init__(self, db):
        self._db = db
        self.reads = []
        self.role_reads = []

    def __getattr__(self, name):
        return getattr(self._db, name)

    def _record(self, kind, query, params):
        self.reads.append((kind, query, tuple(params or ())))
        if "FROM srv_roles WHERE" in query:
            self.role_reads.append((kind, query, tuple(params or ())))

    def fetch_one(self, query, params=()):
        self._record("one", query, params)
        return self._db.fetch_one(query, params)

    def fetch_all(self, query, params=()):
        self._record("all", query, params)
        return self._db.fetch_all(query, params)


def _insert_role(db, manager, server_id, position, deleted=0):
    role_id = manager._generate_id()
    now = manager._get_timestamp()
    db.execute(
        "INSERT INTO srv_roles "
        "(id, server_id, name, permissions, color, hoist, mentionable, "
        "position, is_default, created_at, updated_at, deleted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            role_id,
            server_id,
            f"role-{position}",
            json.dumps({}),
            "#FFFFFF",
            0,
            0,
            position,
            0,
            now,
            now,
            deleted,
        ),
    )
    return role_id


@pytest.mark.unit
def test_whitelist_roles_resolve_in_bounded_chunks_and_dedupe():
    db = _FakeDB()
    ids = list(range(1, 1203)) + [5, 5]  # 1202 distinct ids, two duplicates

    resolved = _resolve_whitelist_roles(db, ids, 7)

    assert len(db.calls) == 3
    assert len(resolved) == 1202
    for sql, params in db.calls:
        assert " IN (" in sql
        assert "server_id = ?" in sql and "deleted = 0" in sql
        assert len(params) - 1 <= _ROLE_LOOKUP_CHUNK_SIZE
        assert params[-1] == 7
    # Duplicates never expand the IN list.
    assert sum(len(params) - 1 for _, params in db.calls) == 1202


@pytest.mark.unit
def test_empty_whitelist_issues_no_query():
    db = _FakeDB()
    assert _resolve_whitelist_roles(db, [], 7) == {}
    assert db.calls == []


@pytest.mark.servers
def test_role_resolution_query_count_drops_from_2n_to_bounded_batches(
    db, server_manager, fresh_users
):
    """Direct before/after measurement on the same database."""
    actor = fresh_users[0]
    server = server_manager.create_server(owner_id=actor.id, name="role batch")
    manager = OnboardingManager(db, server_manager)
    roles = [_insert_role(db, manager, server.id, pos) for pos in range(1, 26)]

    spy = _ReadSpy(db)
    old_manager_db, old_server_db = manager._db, server_manager._db
    old_handler_db = server_manager.role_handler.db
    manager._db = server_manager._db = server_manager.role_handler.db = spy
    try:
        # BEFORE: one get_role per option (role read + membership read each).
        before = 0
        for role_id in roles:
            spy.reads.clear()
            role = server_manager.get_role(role_id, actor.id)
            assert role is not None
            before += len(spy.reads)

        # AFTER: one membership read plus one bounded role lookup.
        spy.reads.clear()
        assert server_manager._is_member(server.id, actor.id)
        resolved = _resolve_whitelist_roles(spy, roles, server.id)
        after = len(spy.reads)
    finally:
        manager._db, server_manager._db = old_manager_db, old_server_db
        server_manager.role_handler.db = old_handler_db

    assert len(resolved) == 25
    assert before == 50  # 25 roles x (SELECT role + SELECT membership)
    assert after == 2
    assert after <= before // 10, (before, after)


@pytest.mark.servers
def test_validation_applies_hierarchy_to_every_resolved_role(
    db, server_manager, fresh_users, monkeypatch
):
    actor = fresh_users[0]
    server = server_manager.create_server(owner_id=actor.id, name="hierarchy")
    manager = OnboardingManager(db, server_manager)
    roles = [_insert_role(db, manager, server.id, pos) for pos in range(1, 26)]

    decisions = []

    def _can_manage(user_roles, target, is_owner):
        decisions.append(target["position"])
        return target["position"] < 5

    monkeypatch.setattr(permission_utils, "can_manage_role", _can_manage)

    # Per-role resolution must be gone from the validation path entirely.
    def _unexpected_get_role(*args, **kwargs):
        raise AssertionError("_validate_step_options must not call get_role")

    monkeypatch.setattr(server_manager, "get_role", _unexpected_get_role)

    with pytest.raises(OnboardingError):
        manager._validate_step_options(
            actor.id,
            server.id,
            OnboardingStepType.SELECT_ROLES,
            {"role_ids": roles},
        )
    # Evaluated lowest position first, and stopped at the first refusal.
    assert decisions == [1, 2, 3, 4, 5]


@pytest.mark.servers
def test_validation_when_all_roles_manageable_makes_one_batch(
    db, server_manager, fresh_users, monkeypatch
):
    actor = fresh_users[0]
    server = server_manager.create_server(owner_id=actor.id, name="all manageable")
    manager = OnboardingManager(db, server_manager)
    roles = [_insert_role(db, manager, server.id, pos) for pos in range(1, 26)]

    decisions = []
    monkeypatch.setattr(
        permission_utils,
        "can_manage_role",
        lambda user_roles, target, is_owner: (
            decisions.append(target["position"]) or True
        ),
    )

    spy = _ReadSpy(db)
    real_db = manager._db
    manager._db = spy
    try:
        manager._validate_step_options(
            actor.id,
            server.id,
            OnboardingStepType.SELECT_ROLES,
            {"role_ids": roles},
        )
    finally:
        manager._db = real_db

    assert decisions == list(range(1, 26))
    assert len(spy.role_reads) == 1
    assert len(spy.role_reads[0][2]) == 26  # 25 ids + server_id


@pytest.mark.servers
def test_missing_deleted_and_foreign_roles_still_rejected(
    db, server_manager, fresh_users, monkeypatch
):
    actor = fresh_users[0]
    server = server_manager.create_server(owner_id=actor.id, name="rejections")
    other = server_manager.create_server(owner_id=actor.id, name="other server")
    manager = OnboardingManager(db, server_manager)
    good = _insert_role(db, manager, server.id, 1)
    deleted = _insert_role(db, manager, server.id, 2, deleted=1)
    foreign = _insert_role(db, manager, other.id, 3)
    monkeypatch.setattr(
        permission_utils, "can_manage_role", lambda *args, **kwargs: True
    )

    for bad in (deleted, foreign, 999999):
        with pytest.raises(RoleNotFoundError):
            manager._validate_step_options(
                actor.id,
                server.id,
                OnboardingStepType.SELECT_ROLES,
                {"role_ids": [good, bad]},
            )


@pytest.mark.servers
def test_non_member_cannot_validate_a_whitelist(db, server_manager, fresh_users):
    owner, outsider = fresh_users[0], fresh_users[1]
    server = server_manager.create_server(owner_id=owner.id, name="outsider")
    manager = OnboardingManager(db, server_manager)
    role = _insert_role(db, manager, server.id, 1)

    with pytest.raises(RoleNotFoundError):
        manager._validate_step_options(
            outsider.id,
            server.id,
            OnboardingStepType.SELECT_ROLES,
            {"role_ids": [role]},
        )
