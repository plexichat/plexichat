"""Regression: an onboarding whitelist may not hand out unheld permissions.

``assign_roles_from_whitelist`` is the one role-grant path that skips
``check_permissions_grantable`` -- the admin/moderator-defined whitelist is the
authorization. Creating a SELECT_ROLES step only requires ``onboarding.manage``,
so a moderator who holds that permission but not ``roles.manage`` could whitelist
a role carrying ``roles.manage`` (placed below their own rank so the position
gate passed), complete the step themselves and self-assign it. That grants a
permission they cannot obtain through create/update/assign, all of which enforce
the grantability rule.

The fix makes whitelist validation require the step author to possess every
permission the whitelisted roles carry, matching the rest of the role system.
"""

import uuid

import pytest


def _register_member(servers, prefix):
    auth = servers._auth
    tag = uuid.uuid4().hex[:8]
    user = auth.register(
        f"{prefix}_{tag}", f"{prefix}_{tag}@example.com", "TestPass123!"
    )
    return user


@pytest.fixture
def setup(fresh_server):
    server, owner, servers = fresh_server

    # The attacker holds onboarding.manage but deliberately NOT roles.manage,
    # and outranks the powerful role so the position gate is satisfied.
    manager_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Onboarding Manager",
        permissions={"onboarding.manage": True},
    )
    servers.move_role(owner.id, manager_role.id, position=10)

    elevated_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Elevated",
        permissions={"roles.manage": True},
    )
    servers.move_role(owner.id, elevated_role.id, position=2)

    moderator = _register_member(servers, "wl_mod")
    servers.add_member(server.id, moderator.id)
    servers.assign_role(owner.id, server.id, moderator.id, manager_role.id)

    return server, owner, moderator, elevated_role, servers


def test_moderator_cannot_whitelist_role_granting_unheld_permission(setup):
    server, owner, moderator, elevated_role, servers = setup

    # Sanity: the moderator meets the *old* gate -- they outrank the elevated
    # role (10 > 2) and can manage it by position -- so only the grantability
    # rule can stop this. Re-fetch: the create_role return value predates the
    # move and holds a stale position.
    assert servers.get_role(elevated_role.id, owner.id).position < 10
    assert servers.has_permission(moderator.id, server.id, "roles.manage") is False

    with pytest.raises(servers.PermissionDeniedError, match="roles.manage"):
        servers.create_onboarding_step(
            user_id=moderator.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.SELECT_ROLES,
            title="Pick a role",
            options={"role_ids": [elevated_role.id]},
        )

    # No whitelist was established and no privilege leaked.
    steps = servers.get_onboarding_steps(moderator.id, server.id)
    assert steps == []
    assert servers.has_permission(moderator.id, server.id, "roles.manage") is False


def test_self_service_path_would_grant_the_escalation(setup):
    """Show the impact: the elevated role is genuinely self-assignable.

    This is the vector the whitelist gate exists to keep out of reach; without
    a safe whitelist the moderator cannot seed ``allowed_role_ids`` on their own.
    """
    server, owner, moderator, elevated_role, servers = setup

    assigned = servers.role_handler.assign_roles_from_whitelist(
        moderator.id, server.id, [elevated_role.id], [elevated_role.id]
    )

    assert assigned == [elevated_role.id]
    assert servers.has_permission(moderator.id, server.id, "roles.manage") is True


def test_owner_can_still_whitelist_role_granting_manage_permission(setup):
    """No regression: the owner holds every permission and may whitelist it."""
    server, owner, moderator, elevated_role, servers = setup

    step = servers.create_onboarding_step(
        user_id=owner.id,
        server_id=server.id,
        step_type=servers.OnboardingStepType.SELECT_ROLES,
        title="Pick a role",
        options={"role_ids": [elevated_role.id]},
    )

    assert step is not None
    assert elevated_role.id in step.options["role_ids"]
