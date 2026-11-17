"""Regressions for authorization holes in channel overrides and onboarding.

Covers:
- channel override allow/deny dicts are validated like role permissions, so a
  channels.manage holder cannot grant themselves ``administrator``;
- a channel override member target must be a real member of the server and
  must respect the member hierarchy (no IDOR / rank bypass);
- a SELECT_ROLES onboarding whitelist cannot offer roles above the step
  creator's own rank (bulk self-escalation).
"""

import pytest


class TestChannelOverrideGrantability:
    """A channels.manage holder must not be able to mint permissions."""

    def test_override_allow_rejects_unknown_and_wildcard_keys(
        self, server_with_channels
    ):
        server, owner, admin_user, _, _, general, _, _, _, servers = (
            server_with_channels
        )
        for bad in ({"not.a.permission": True}, {"*": True}, {"messages.*": True}):
            with pytest.raises(servers.InvalidPermissionKeyError):
                servers.set_channel_override(
                    user_id=admin_user.id,
                    channel_id=general.id,
                    target_type="member",
                    target_id=admin_user.id,
                    allow=bad,
                )

    def test_override_allow_cannot_grant_unheld_permission(self, server_with_channels):
        server, owner, admin_user, _, _, general, _, _, _, servers = (
            server_with_channels
        )
        # admin_user holds channels.manage but NOT administrator. Granting
        # administrator via a self-target override used to unlock every
        # permission in the channel (apply_channel_overrides honours it).
        assert (
            servers.has_permission(
                admin_user.id, server.id, "administrator", general.id
            )
            is False
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.set_channel_override(
                user_id=admin_user.id,
                channel_id=general.id,
                target_type="member",
                target_id=admin_user.id,
                allow={"administrator": True},
            )

    def test_owner_can_still_set_a_legitimate_override(self, server_with_channels):
        server, owner, _, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )
        override = servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            allow={"messages.send": True},
        )
        assert override.allow.get("messages.send") is True


class TestChannelOverrideTargetGuard:
    """Member targets need membership + hierarchy checks, not just a raw ID."""

    def test_member_target_must_be_member_of_the_server(self, server_with_channels):
        server, owner, admin_user, _, outsider, general, _, _, _, servers = (
            server_with_channels
        )
        with pytest.raises(servers.MemberNotFoundError):
            servers.set_channel_override(
                user_id=admin_user.id,
                channel_id=general.id,
                target_type="member",
                target_id=outsider.id,
            )

    def test_member_target_respects_hierarchy(self, server_with_channels):
        server, owner, admin_user, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )

        # Give member_user a channels.manage role BELOW a new high-ranked role
        # that admin_user holds. The actor can manage channels but not the
        # higher-ranked target member.
        low_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Low Channel Mod",
            permissions={"channels.manage": True},
        )
        high_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="High Rank",
            permissions={"messages.send": True},
        )
        assert high_role.position > low_role.position
        servers.assign_role(owner.id, server.id, member_user.id, low_role.id)
        servers.assign_role(owner.id, server.id, admin_user.id, high_role.id)

        with pytest.raises(servers.RoleHierarchyError):
            servers.set_channel_override(
                user_id=member_user.id,
                channel_id=general.id,
                target_type="member",
                target_id=admin_user.id,
                deny={"messages.send": True},
            )


class TestOnboardingWhitelistHierarchy:
    """Bulk role grants via onboarding must respect role hierarchy."""

    def test_step_cannot_offer_role_above_creator_rank(self, server_with_channels):
        server, owner, admin_user, member_user, _, _, _, _, _, servers = (
            server_with_channels
        )

        manager_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Onboarding Manager",
            permissions={"onboarding.manage": True},
        )
        privileged_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Privileged",
            permissions={"messages.manage": True},
        )
        assert privileged_role.position > manager_role.position
        servers.assign_role(owner.id, server.id, member_user.id, manager_role.id)

        with pytest.raises(servers.OnboardingError):
            servers.create_onboarding_step(
                user_id=member_user.id,
                server_id=server.id,
                step_type=servers.OnboardingStepType.SELECT_ROLES,
                title="Pick a role",
                options={"role_ids": [privileged_role.id]},
            )
