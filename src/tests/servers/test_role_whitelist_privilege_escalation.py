"""Regression: self-service role whitelist must not grant wildcard roles.

``has_permission()`` expands a truthy ``*`` permission into every server
permission. The whitelist path used to reject only the literal
``administrator`` key, so a legacy/imported role carrying ``{"*": true}``
could be self-assigned during onboarding and hand the caller ``roles.manage``.
"""

import json

import pytest


def _insert_role(db, manager, server_id, name, permissions):
    now = manager._get_timestamp()
    role_id = manager._generate_id()
    db.execute(
        "INSERT INTO srv_roles "
        "(id, server_id, name, permissions, color, hoist, mentionable, "
        "position, is_default, created_at, updated_at, deleted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (
            role_id,
            server_id,
            name,
            json.dumps(permissions),
            "#FFFFFF",
            0,
            0,
            10,
            0,
            now,
            now,
        ),
    )
    return role_id


@pytest.fixture
def whitelist_setup(db, server_manager, fresh_users):
    owner, member, _presence = fresh_users
    server = server_manager.create_server(owner_id=owner.id, name="Whitelist Test")
    server_manager.add_member(server.id, member.id)
    return db, server_manager, server, member


def test_wildcard_role_not_self_assignable(whitelist_setup):
    db, manager, server, member = whitelist_setup
    role_id = _insert_role(db, manager, server.id, "Wildcard", {"*": True})

    assigned = manager.role_handler.assign_roles_from_whitelist(
        member.id, server.id, [role_id], [role_id]
    )

    assert assigned == []
    assert role_id not in [r.id for r in manager.get_member_roles(server.id, member.id)]


def test_literal_administrator_role_still_refused(whitelist_setup):
    db, manager, server, member = whitelist_setup
    role_id = _insert_role(db, manager, server.id, "Admin", {"administrator": True})

    assigned = manager.role_handler.assign_roles_from_whitelist(
        member.id, server.id, [role_id], [role_id]
    )

    assert assigned == []


def test_benign_role_still_self_assignable(whitelist_setup):
    db, manager, server, member = whitelist_setup
    role_id = _insert_role(db, manager, server.id, "Helper", {"messages.send": True})

    assigned = manager.role_handler.assign_roles_from_whitelist(
        member.id, server.id, [role_id], [role_id]
    )

    assert assigned == [role_id]
    assert role_id in [r.id for r in manager.get_member_roles(server.id, member.id)]
