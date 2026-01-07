"""
Tests for exemptions system.
"""

import pytest

from src.core.automod import RuleType


@pytest.mark.automod
class TestExemptions:
    """Tests for exemption system."""

    def test_role_exemption(
        self, automod_module, test_server_for_automod, modules, user_pool
    ):
        """Test role exemption from rules."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()

        modules.servers.add_member(server.id, user.id)

        exempt_role = modules.servers.create_role(
            user_id=owner.id, server_id=server.id, name="Exempt Role", permissions={}
        )

        modules.servers.assign_role(owner.id, server.id, user.id, exempt_role.id)

        automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Test Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["test"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "delete_message"}],
            exempt_roles=[exempt_role.id],
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="test message",
        )

        assert result.passed

    def test_channel_exemption(self, automod_module, test_server_for_automod, modules):
        """Test channel exemption from rules."""
        server, channel, owner = test_server_for_automod

        exempt_channel = modules.servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="exempt-channel",
            channel_type=modules.servers.ChannelType.TEXT,
        )

        automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Test Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["test"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "delete_message"}],
            exempt_channels=[exempt_channel.id],
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=exempt_channel.id,
            user_id=owner.id,
            content="test message",
        )

        assert result.passed

    def test_global_exemption(
        self, automod_module, test_server_for_automod, modules, user_pool
    ):
        """Test global exemption from all rules."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()

        modules.servers.add_member(server.id, user.id)

        exempt_role = modules.servers.create_role(
            user_id=owner.id, server_id=server.id, name="Trusted", permissions={}
        )

        modules.servers.assign_role(owner.id, server.id, user.id, exempt_role.id)

        automod_module.add_exemption(
            user_id=owner.id,
            server_id=server.id,
            target_type="role",
            target_id=exempt_role.id,
            rule_id=None,
        )

        automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Test Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["test"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "delete_message"}],
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="test message",
        )

        assert result.passed

    def test_owner_always_exempt(self, automod_module, test_server_for_automod):
        """Test server owner is always exempt."""
        server, channel, owner = test_server_for_automod

        automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Test Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["test"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "delete_message"}],
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="test message",
        )

        assert result.passed
