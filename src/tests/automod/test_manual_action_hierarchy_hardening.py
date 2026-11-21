"""Regression tests: manual automod actions must respect server role hierarchy.

``trigger_action`` executes the ban/kick/timeout raw-SQL fallbacks under a
system-context flag. Those fallbacks intentionally skip the servers module's
``can_manage_member`` hierarchy check, so without an explicit guard in
``trigger_action`` a moderator holding only ``members.ban`` / ``members.kick``
could sanction a member whose role outranks their own. Every normal servers
code path (``ban_member``, ``kick_member``, ``update_member``) enforces this
hierarchy; a manual automod action must not be a way around it.
"""

import pytest

from src.core.automod.models import ActionType


@pytest.mark.automod
class TestManualActionHierarchy:
    def _make_members(self, modules, user_pool, server, owner, permission):
        moderator = user_pool.get_user()
        senior = user_pool.get_user()
        modules.servers.add_member(server.id, moderator.id)
        modules.servers.add_member(server.id, senior.id)

        mod_role = modules.servers.create_role(
            owner.id,
            server.id,
            name="Moderator",
            permissions={permission: True},
        )
        modules.servers.assign_role(owner.id, server.id, moderator.id, mod_role.id)

        # A higher role with no permissions: it exists purely to establish a
        # hierarchy the moderator must not be allowed to reach past.
        senior_role = modules.servers.create_role(
            owner.id, server.id, name="Senior", permissions={}
        )
        modules.servers.assign_role(owner.id, server.id, senior.id, senior_role.id)

        assert senior_role.position > mod_role.position
        assert (
            modules.servers.has_permission(moderator.id, server.id, permission) is True
        )
        return moderator, senior

    @pytest.mark.parametrize(
        "action_type,permission",
        [
            (ActionType.BAN_USER, "members.ban"),
            (ActionType.KICK_USER, "members.kick"),
            (ActionType.TIMEOUT_USER, "members.kick"),
        ],
    )
    def test_manual_action_refuses_senior_target(
        self,
        automod_manager,
        test_server,
        user_pool,
        modules,
        action_type,
        permission,
    ):
        server, owner = test_server
        moderator, senior = self._make_members(
            modules, user_pool, server, owner, permission
        )

        assert (
            automod_manager.trigger_action(
                user_id=moderator.id,
                server_id=server.id,
                target_user_id=senior.id,
                action_type=action_type,
                reason="hierarchy regression",
            )
            is False
        )
        # Refused before execution, so nothing was audited either.
        assert automod_manager.get_audit_log(server.id) == []

    def test_moderator_can_still_act_on_lower_role_member(
        self, automod_manager, test_server, user_pool, modules
    ):
        server, owner = test_server
        moderator = user_pool.get_user()
        junior = user_pool.get_user()
        modules.servers.add_member(server.id, moderator.id)
        modules.servers.add_member(server.id, junior.id)
        mod_role = modules.servers.create_role(
            owner.id,
            server.id,
            name="Moderator",
            permissions={"members.kick": True},
        )
        modules.servers.assign_role(owner.id, server.id, moderator.id, mod_role.id)

        assert (
            automod_manager.trigger_action(
                user_id=moderator.id,
                server_id=server.id,
                target_user_id=junior.id,
                action_type=ActionType.TIMEOUT_USER,
                reason="legitimate moderation",
            )
            is True
        )

    def test_owner_can_still_act_on_senior_member(
        self, automod_manager, test_server, user_pool, modules
    ):
        server, owner = test_server
        senior = user_pool.get_user()
        modules.servers.add_member(server.id, senior.id)
        modules.servers.create_role(owner.id, server.id, name="Senior", permissions={})

        assert (
            automod_manager.trigger_action(
                user_id=owner.id,
                server_id=server.id,
                target_user_id=senior.id,
                action_type=ActionType.BAN_USER,
                reason="owner moderation",
            )
            is True
        )
