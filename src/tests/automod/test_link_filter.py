"""Tests for automod link/invite filter rules."""

import pytest

from src.core.automod.models import RuleType


@pytest.mark.automod
class TestLinkFilter:
    """Tests for invite link and external link filter rules."""

    def test_create_invite_link_rule(self, automod_manager, test_server):
        """Test creating an invite link filter rule."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="No Invite Links",
            rule_type=RuleType.INVITE_LINKS,
            rule_config={},
            actions=[{"action_type": "delete_message"}],
        )
        assert rule.name == "No Invite Links"
        assert rule.rule_type == RuleType.INVITE_LINKS

    def test_create_external_link_rule(self, automod_manager, test_server):
        """Test creating an external link filter rule."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="No External Links",
            rule_type=RuleType.EXTERNAL_LINKS,
            rule_config={"allowed_domains": ["example.com"]},
            actions=[{"action_type": "delete_message"}, {"action_type": "log_only"}],
        )
        assert rule.name == "No External Links"
        assert rule.rule_type == RuleType.EXTERNAL_LINKS

    def test_invite_link_rule_retrieved(self, automod_manager, test_server):
        """Test invite link rule can be retrieved after creation."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Block Invites",
            rule_type=RuleType.INVITE_LINKS,
            rule_config={},
            actions=[{"action_type": "delete_message"}],
        )
        retrieved = automod_manager.get_rule(rule.id)
        assert retrieved is not None
        assert retrieved.rule_type == RuleType.INVITE_LINKS
