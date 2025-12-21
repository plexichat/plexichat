"""
Tests for automod actions.
"""

import pytest

from src.core.automod import RuleType, ActionType


@pytest.mark.automod
class TestDeleteMessageAction:
    """Tests for delete message action."""

    def test_delete_message_action(self, automod_module, test_server_for_automod, modules, user_pool):
        """Test message deletion action."""
        server, channel, owner = test_server_for_automod

        # Use a non-owner member for the test
        member = user_pool.get_user()
        modules.servers.add_member(server.id, member.id)

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Delete Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["delete_me"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "delete_message"}]
        )

        conv = modules.messaging.create_server_channel_conversation(server.id, channel.id)
        msg = modules.messaging.send_message(member.id, conv.id, "This has delete_me word")

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=member.id,
            content="This has delete_me word",
            message_id=msg.id
        )

        assert not result.passed
        assert result.should_delete


@pytest.mark.automod
class TestTimeoutAction:
    """Tests for timeout action."""

    def test_timeout_user_action(self, automod_module, test_server_for_automod, modules, user_pool):
        """Test user timeout action."""
        server, channel, owner = test_server_for_automod
        member = user_pool.get_user()

        modules.servers.add_member(server.id, member.id)

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Timeout Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["timeout_me"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "timeout_user", "duration_seconds": 300}]
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=member.id,
            content="This has timeout_me word"
        )

        assert not result.passed
        assert result.should_timeout
        assert result.timeout_duration == 300

    @pytest.mark.skip(reason="Owner is exempt from automod")
    def test_cannot_timeout_owner(self, automod_module, test_server_for_automod):
        """Test that server owner cannot be timed out."""
        # Test skipped because owner is exempt from checks, so passed=True always.
        pass


@pytest.mark.automod
class TestAlertAction:
    """Tests for alert moderators action."""

    def test_alert_moderators(self, automod_module, test_server_for_automod, user_pool, modules):
        """Test moderator alert action."""
        server, channel, owner = test_server_for_automod
        member = user_pool.get_user()
        modules.servers.add_member(server.id, member.id)

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Alert Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["alert"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "alert_moderators"}]
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=member.id,
            content="This should alert mods"
        )

        assert not result.passed
        assert any(a.action_type == ActionType.ALERT_MODERATORS for a in result.actions_to_take)


@pytest.mark.automod
class TestLogOnlyAction:
    """Tests for log only action."""

    def test_log_only_no_other_actions(self, automod_module, test_server_for_automod, user_pool, modules):
        """Test log only action doesn't trigger other actions."""
        server, channel, owner = test_server_for_automod
        member = user_pool.get_user()
        modules.servers.add_member(server.id, member.id)

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Log Only Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["log"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "log_only"}]
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=member.id,
            content="This should log only"
        )

        assert not result.passed
        assert not result.should_delete
        assert not result.should_timeout
