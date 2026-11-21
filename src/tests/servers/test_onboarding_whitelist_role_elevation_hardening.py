"""Regression: a role elevated after a whitelist is saved cannot be handed out.

``assign_roles_from_whitelist`` is the one role-grant path with no live
``members.manage_roles``/grantability check -- the admin-defined whitelist is
the authorization. The whitelist author's grants are validated when the step
is created, but a role's permissions may be edited afterwards by an admin who
holds the new grants. Without re-validating at completion time, a member could
self-assign the elevated role and inherit permissions the author never held.
"""

import uuid

import pytest


def _member(servers, prefix):
    tag = uuid.uuid4().hex[:8]
    user = servers._auth.register(
        f"{prefix}_{tag}", f"{prefix}_{tag}@example.com", "TestPass123!"
    )
    return user


@pytest.fixture
def setup(fresh_server):
    server, owner, servers = fresh_server

    # The role is harmless when the whitelist is authored.
    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Pickable",
        permissions={"messages.send": True},
    )
    servers.move_role(owner.id, role.id, 3)

    # The whitelist author holds onboarding.manage and messages.send, but NOT
    # roles.manage. Their rank (10) outranks the pickable role (3), so both
    # gates at whitelist creation pass.
    manager_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Onboarding Manager",
        permissions={"onboarding.manage": True, "messages.send": True},
    )
    servers.move_role(owner.id, manager_role.id, 10)

    author = _member(servers, "wl_author")
    server_module = servers
    server_module.add_member(server.id, author.id)
    servers.assign_role(owner.id, server.id, author.id, manager_role.id)

    step = servers.create_onboarding_step(
        user_id=author.id,
        server_id=server.id,
        step_type=servers.OnboardingStepType.SELECT_ROLES,
        title="Pick a role",
        options={"role_ids": [role.id]},
    )
    return server, owner, author, role, step, servers


def test_elevated_role_not_self_assignable(setup):
    server, owner, author, role, step, servers = setup

    # Sanity: the author genuinely lacks roles.manage at whitelist-creation
    # time; only a later edit of the role could introduce it.
    assert servers.has_permission(author.id, server.id, "roles.manage") is False
    assert servers.get_role(role.id, owner.id).permissions == {"messages.send": True}

    # An admin raises the role's permissions after the whitelist was authored.
    servers.update_role(
        user_id=owner.id,
        role_id=role.id,
        permissions={"messages.send": True, "roles.manage": True},
    )

    joiner = _member(servers, "wl_joiner")
    servers.add_member(server.id, joiner.id)

    with pytest.raises(servers.OnboardingError):
        servers.complete_onboarding_step(
            user_id=joiner.id,
            server_id=server.id,
            step_id=step.id,
            response={"selected_roles": [role.id]},
        )

    assert servers.has_permission(joiner.id, server.id, "roles.manage") is False
    assigned = [r.id for r in servers.get_member_roles(server.id, joiner.id)]
    assert role.id not in assigned


def test_unchanged_whitelist_still_grants(setup):
    """No regression: a role whose permissions did not change is assignable."""
    server, owner, author, role, step, servers = setup

    joiner = _member(servers, "wl_ok")
    servers.add_member(server.id, joiner.id)

    servers.complete_onboarding_step(
        user_id=joiner.id,
        server_id=server.id,
        step_id=step.id,
        response={"selected_roles": [role.id]},
    )

    assigned = [r.id for r in servers.get_member_roles(server.id, joiner.id)]
    assert role.id in assigned
    assert servers.has_permission(joiner.id, server.id, "messages.send") is True
