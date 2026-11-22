"""Force-password-change must not bypass the admin role-position hierarchy.

Every other admin-targeting mutation (admin_user.update/delete/toggle_status,
ban/unban admin) refuses to act on an admin whose highest role position is at
or above the actor's. ``force_password_change`` is the one support-tool path
that skipped that gate: any holder of ``admin.edit`` (e.g. the seeded
``support_admin`` role at position 10) could force a password change on a
``super_admin`` (position 100), evicting a superior account through a path
that deliberately carries no hierarchy check.

These tests pin the public wrapper to the same ``can_manage_admin`` decision
the other mutations use, and keep the legitimate downward case working.
"""

import pytest

import src.core.admin as admin_module


pytestmark = [pytest.mark.security, pytest.mark.unit]


class _HierarchyDB:
    """Minimal db double that answers the two position lookups by id."""

    def __init__(self, positions):
        self.positions = positions

    def fetch_one(self, sql, params):
        if "MAX(r.position)" in sql:
            return {"max_pos": self.positions.get(int(params[0]), 0)}
        return {"id": params[0]}


@pytest.fixture
def hierarchy(monkeypatch):
    db = _HierarchyDB({10: 10, 20: 5, 100: 100})
    monkeypatch.setattr(admin_module, "_db", db, raising=False)
    monkeypatch.setattr(admin_module, "_setup_complete", True, raising=False)
    calls = []

    def _fake_target(**kwargs):
        calls.append(kwargs)
        return True, "done"

    # Stub only the mutation so this module tests the wrapper's authorization
    # decision in isolation (the mutation's own invariants have their own
    # regression tests).
    monkeypatch.setattr(
        "src.core.admin.auth.password_mixin.force_password_change_target",
        _fake_target,
    )
    return calls


def test_support_admin_cannot_force_password_change_on_super_admin(hierarchy):
    ok, message = admin_module.force_password_change(10, 100)

    assert ok is False
    assert message
    assert hierarchy == [], "mutation ran despite the hierarchy refusal"


def test_equal_position_cannot_force_password_change(hierarchy):
    ok, _ = admin_module.force_password_change(10, 10)

    assert ok is False
    assert hierarchy == []


def test_super_admin_can_still_force_password_change_on_support_admin(hierarchy):
    ok, message = admin_module.force_password_change(100, 10)

    assert ok is True, message
    assert len(hierarchy) == 1
    assert hierarchy[0]["acting_admin_id"] == 100
    assert hierarchy[0]["target_admin_id"] == 10
