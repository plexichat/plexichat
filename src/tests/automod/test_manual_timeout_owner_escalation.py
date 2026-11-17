"""Regression tests: manual automod actions must not outrank the server owner.

``trigger_action`` runs on behalf of a named moderator and asserts the
``__system_context__`` flag so the ban/kick/delete raw-SQL fallbacks work.
``TimeoutUserAction.can_execute`` reads that same flag to decide whether the
server owner may be sanctioned: ``(system)`` automod rules may time out the
owner, but a *manual* action by a moderator must not. Because trigger_action
set the flag for every action type, a moderator holding only ``members.kick``
could time out the owner — something the ban and kick executors refuse
outright.
"""

import pytest

from src.core.automod.models import ActionType


@pytest.mark.automod
class TestManualTimeoutOwnerEscalation:
    def test_manual_timeout_cannot_target_server_owner(
        self, automod_manager, test_server, user_pool, modules
    ):
        server, owner = test_server
        moderator = user_pool.get_user()
        modules.servers.add_member(server.id, moderator.id)
        role = modules.servers.create_role(
            owner.id,
            server.id,
            name="Kicker",
            permissions={"members.kick": True},
        )
        modules.servers.assign_role(owner.id, server.id, moderator.id, role.id)

        assert (
            modules.servers.has_permission(moderator.id, server.id, "members.kick")
            is True
        )
        # The owner is a member (create_server adds them), so the only reason
        # this must be refused is the owner protection, not a missing member.
        assert (
            automod_manager.trigger_action(
                user_id=moderator.id,
                server_id=server.id,
                target_user_id=owner.id,
                action_type=ActionType.TIMEOUT_USER,
                reason="must be refused",
            )
            is False
        )

    def test_manual_timeout_still_works_on_regular_member(
        self, automod_manager, test_server, user_pool, modules
    ):
        server, owner = test_server
        moderator = user_pool.get_user()
        victim = user_pool.get_user()
        modules.servers.add_member(server.id, moderator.id)
        modules.servers.add_member(server.id, victim.id)
        role = modules.servers.create_role(
            owner.id,
            server.id,
            name="Kicker",
            permissions={"members.kick": True},
        )
        modules.servers.assign_role(owner.id, server.id, moderator.id, role.id)

        assert (
            automod_manager.trigger_action(
                user_id=moderator.id,
                server_id=server.id,
                target_user_id=victim.id,
                action_type=ActionType.TIMEOUT_USER,
                reason="legitimate moderation",
            )
            is True
        )

    def test_automated_timeout_of_owner_remains_allowed(
        self, automod_manager, test_server
    ):
        """System-driven automod rules keep the ability to time out the owner."""
        from src.core.automod.actions.timeout import TimeoutUserAction
        from src.core.automod.models import (
            RuleAction,
            RuleType,
            Violation,
            ViolationSeverity,
        )

        server, owner = test_server
        action = RuleAction(action_type=ActionType.TIMEOUT_USER, duration_seconds=60)
        violation = Violation(
            id=1,
            server_id=server.id,
            channel_id=0,
            user_id=owner.id,
            message_id=None,
            rule_id=0,
            rule_type=RuleType.KEYWORD,
            matched_content="spam",
            actions_taken=[],
            severity=ViolationSeverity.MEDIUM,
            created_at=0,
        )
        executor = TimeoutUserAction(
            automod_manager._db, automod_manager._servers, None, None
        )
        ok, _ = executor.can_execute(
            action, violation, context={"__system_context__": True}
        )
        assert ok is True
