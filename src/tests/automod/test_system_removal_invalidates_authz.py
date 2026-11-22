"""System-context AutoMod member removals must invalidate authorization caches.

The ban/kick executors fall back to raw SQL when no bot actor is available.
That path removes ``srv_members``/``srv_member_roles`` but historically skipped
every permission cache tier, so a just-kicked/banned member kept answering
with a cached channel GRANT until the TTL expired. These tests prime that GRANT
and assert the very next permission check DENIES after the system removal.
"""

import pytest

from src.core.automod.actions.ban import BanUserAction
from src.core.automod.actions.kick import KickUserAction
from src.core.automod.models import (
    ActionType,
    RuleAction,
    RuleType,
    Violation,
    ViolationSeverity,
)
from src.core.servers.channel_access_cache import get_channel_access_cache


def _violation(server, victim):
    return Violation(
        id=1,
        server_id=server.id,
        channel_id=0,
        user_id=victim.id,
        message_id=None,
        rule_id=0,
        rule_type=RuleType.KEYWORD,
        matched_content="spam",
        actions_taken=[],
        severity=ViolationSeverity.MEDIUM,
        created_at=0,
    )


def _prime_channel_grant(modules, owner, server, channel, victim):
    role = modules.servers.create_role(
        owner.id,
        server.id,
        name="Poster",
        permissions={"messages.send": True},
    )
    modules.servers.assign_role(owner.id, server.id, victim.id, role.id)
    # Warm every tier with an explicit GRANT at the channel scope.
    assert (
        modules.servers.has_permission(
            victim.id, server.id, "messages.send", channel.id
        )
        is True
    )
    assert (
        get_channel_access_cache().get(
            server.id, victim.id, channel.id, "messages.send"
        )
        is True
    )


@pytest.mark.automod
class TestSystemRemovalInvalidatesAuthz:
    def test_system_kick_revokes_cached_channel_grant(
        self, modules, automod_manager, test_server_for_automod, user_pool
    ):
        server, channel, owner = test_server_for_automod
        victim = user_pool.get_user()
        modules.servers.add_member(server.id, victim.id)
        _prime_channel_grant(modules, owner, server, channel, victim)

        executor = KickUserAction(automod_manager._db, modules.servers, None, None)
        action = RuleAction(action_type=ActionType.KICK_USER)
        # No bot_user_id -> raw-SQL system fallback is taken.
        assert (
            executor.execute(
                action, _violation(server, victim), context={"__system_context__": True}
            )
            is True
        )

        assert (
            modules.servers.has_permission(
                victim.id, server.id, "messages.send", channel.id
            )
            is False
        )

    def test_system_ban_revokes_cached_channel_grant(
        self, modules, automod_manager, test_server_for_automod, user_pool
    ):
        server, channel, owner = test_server_for_automod
        victim = user_pool.get_user()
        modules.servers.add_member(server.id, victim.id)
        _prime_channel_grant(modules, owner, server, channel, victim)

        executor = BanUserAction(automod_manager._db, modules.servers, None, None)
        action = RuleAction(action_type=ActionType.BAN_USER)
        assert (
            executor.execute(
                action, _violation(server, victim), context={"__system_context__": True}
            )
            is True
        )

        assert (
            modules.servers.has_permission(
                victim.id, server.id, "messages.send", channel.id
            )
            is False
        )
