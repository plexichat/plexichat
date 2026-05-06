"""Tests for automod security - permission checks and authorization."""

import pytest

from src.core.automod.models import RuleType
from src.core.automod.exceptions import RuleNotFoundError


@pytest.mark.automod
class TestSecurity:
    """Tests for automod security and permission enforcement."""

    def test_delete_nonexistent_rule_raises(self, automod_manager, test_server):
        """Test deleting a nonexistent rule raises error."""
        server, owner = test_server
        with pytest.raises(RuleNotFoundError):
            automod_manager.delete_rule(owner.id, 9999999)

    def test_get_nonexistent_rule_returns_none(self, automod_manager, test_server):
        """Test getting nonexistent rule returns None."""
        result = automod_manager.get_rule(9999999)
        assert result is None

    def test_enable_disable_rule(self, automod_manager, test_server):
        """Test enabling and disabling a rule."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Toggle Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["test"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )
        assert rule.enabled is True

        disabled = automod_manager.set_rule_enabled(owner.id, rule.id, False)
        assert disabled.enabled is False

        enabled = automod_manager.set_rule_enabled(owner.id, rule.id, True)
        assert enabled.enabled is True

    def test_disable_nonexistent_rule_raises(self, automod_manager, test_server):
        """Test disabling nonexistent rule raises error."""
        server, owner = test_server
        with pytest.raises(RuleNotFoundError):
            automod_manager.set_rule_enabled(owner.id, 9999999, False)

    def test_server_owner_exempt(self, automod_manager, test_server):
        """Test that server owner is exempt from automod checks."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Owner Exempt Test",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["badword"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1", (server.id,)
        )
        channel_id = channel["id"] if channel else 0

        # Owner should be exempt
        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=owner.id,
            content="badword should not trigger for owner",
        )
        assert result.passed

    def test_rule_isolation_between_servers(
        self, automod_manager, auth_manager, server_manager
    ):
        """Test rules from one server don't affect another."""
        from unittest.mock import patch
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner1 = auth_manager.register(
                "srv_owner1_sec", "o1s@test.com", "TestPass123!"
            )
            owner2 = auth_manager.register(
                "srv_owner2_sec", "o2s@test.com", "TestPass123!"
            )

        server1 = server_manager.create_server(owner1.id, "Server 1")
        server2 = server_manager.create_server(owner2.id, "Server 2")

        automod_manager.create_rule(
            user_id=owner1.id,
            server_id=server1.id,
            name="Server1 Only Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["uniquetrigger"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        # Server2 should have no rules
        rules2 = automod_manager.get_server_rules(server2.id)
        assert len(rules2) == 0
