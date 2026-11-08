"""Regression coverage for the admin password-reset role assignment.

A forced reset deletes and recreates the built-in ``admin`` row. The system
roles are not recreated, so the reset must explicitly assign the existing
``super_admin`` role to the new account; otherwise login succeeds but every
permission-gated admin route returns 403.
"""

from unittest.mock import MagicMock

from src.core.admin.auth.auth_mixin import _seed_default_admin_roles


def _seed_db():
    """Return a mock DB representing an install that already has system roles."""
    db = MagicMock()

    def fetch_one(query, params=()):
        if "COUNT(*)" in query:
            return {"c": 2}
        if params == ("super_admin",):
            return {"id": 371296595146313728}
        if params == ("support_admin",):
            return {"id": 371296595167285248}
        if "admin_role_assignments" in query:
            return None
        raise AssertionError(f"unexpected query: {query}")

    db.fetch_one.side_effect = fetch_one
    return db


def test_reset_assigns_existing_super_admin_to_recreated_admin():
    db = _seed_db()

    _seed_default_admin_roles(db, 377919431529074688, 1_762_600_000)

    role_inserts = [
        call
        for call in db.execute.call_args_list
        if "INSERT INTO admin_roles" in call.args[0]
    ]
    assert role_inserts == []

    assignment_inserts = [
        call
        for call in db.execute.call_args_list
        if "INSERT INTO admin_role_assignments" in call.args[0]
    ]
    assert len(assignment_inserts) == 1
    assert assignment_inserts[0].args[1] == (
        377919431529074688,
        371296595146313728,
        1_762_600_000,
        377919431529074688,
    )


def test_seed_does_not_duplicate_an_existing_super_admin_assignment():
    db = _seed_db()

    def fetch_one(query, params=()):
        if "COUNT(*)" in query:
            return {"c": 2}
        if params == ("super_admin",):
            return {"id": 371296595146313728}
        if params == ("support_admin",):
            return {"id": 371296595167285248}
        if "admin_role_assignments" in query:
            return {"assigned": 1}
        raise AssertionError(f"unexpected query: {query}")

    db.fetch_one.side_effect = fetch_one

    _seed_default_admin_roles(db, 377919431529074688, 1_762_600_000)

    assignment_inserts = [
        call
        for call in db.execute.call_args_list
        if "INSERT INTO admin_role_assignments" in call.args[0]
    ]
    assert assignment_inserts == []
