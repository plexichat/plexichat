"""
Tests for link filtering rules.
"""

import pytest

from src.core import automod
from src.core.automod import RuleType
from src.core.automod.rules.links import InviteLinkRule, ExternalLinkRule


@pytest.mark.automod
class TestInviteLinkRule:
    """Tests for InviteLinkRule."""

    def test_blocks_invite_code(self, automod_module, test_server_for_automod):
        """Test invite codes are blocked when in known list."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Block Invites",
            rule_type=RuleType.INVITE_LINKS,
            rule_config={
                "block_all": True,
                "code_length": 8
            },
            actions=[{"action_type": "delete_message"}]
        )

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Join my server: abc12345",
            context={"known_invite_codes": ["abc12345"]}
        )

        assert not result.passed

    def test_allows_whitelisted_codes(self, automod_module, test_server_for_automod):
        """Test whitelisted invite codes pass."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Block Invites With Whitelist",
            rule_type=RuleType.INVITE_LINKS,
            rule_config={
                "block_all": True,
                "allowed_codes": ["allowed1"]
            },
            actions=[{"action_type": "delete_message"}]
        )

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Join partner server: allowed1",
            context={"known_invite_codes": ["allowed1"]}
        )

        assert result.passed

    def test_no_invite_passes(self, automod_module, test_server_for_automod):
        """Test message without invites passes."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Block Invites",
            rule_type=RuleType.INVITE_LINKS,
            rule_config={"block_all": True},
            actions=[{"action_type": "delete_message"}]
        )

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Just a normal message"
        )

        assert result.passed


@pytest.mark.automod
class TestExternalLinkRule:
    """Tests for ExternalLinkRule."""

    def test_blacklist_mode(self, automod_module, test_server_for_automod):
        """Test blacklist mode blocks specified domains."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Block Bad Links",
            rule_type=RuleType.EXTERNAL_LINKS,
            rule_config={
                "mode": "blacklist",
                "blacklist": ["malware.com", "phishing.net"]
            },
            actions=[{"action_type": "delete_message"}]
        )

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Check out https://malware.com/download"
        )

        assert not result.passed

    def test_whitelist_mode(self, automod_module, test_server_for_automod):
        """Test whitelist mode only allows specified domains."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Whitelist Links",
            rule_type=RuleType.EXTERNAL_LINKS,
            rule_config={
                "mode": "whitelist",
                "whitelist": ["github.com", "docs.example.com"]
            },
            actions=[{"action_type": "delete_message"}]
        )

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Check out https://random-site.com/page"
        )

        assert not result.passed

    def test_whitelisted_domain_passes(self, automod_module, test_server_for_automod):
        """Test whitelisted domains pass in whitelist mode."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Whitelist Links",
            rule_type=RuleType.EXTERNAL_LINKS,
            rule_config={
                "mode": "whitelist",
                "whitelist": ["github.com"]
            },
            actions=[{"action_type": "delete_message"}]
        )

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Check out https://github.com/repo"
        )

        assert result.passed

    def test_subdomain_matching(self, automod_module, test_server_for_automod):
        """Test subdomain matching works."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Block Domain",
            rule_type=RuleType.EXTERNAL_LINKS,
            rule_config={
                "mode": "blacklist",
                "blacklist": ["evil.com"]
            },
            actions=[{"action_type": "delete_message"}]
        )

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Check out https://sub.evil.com/page"
        )

        assert not result.passed


@pytest.mark.automod
class TestLinkRuleValidation:
    """Tests for link rule config validation."""

    def test_valid_invite_config(self):
        """Test valid invite rule config."""
        valid, issues = InviteLinkRule.validate_config({
            "block_all": True,
            "allowed_codes": ["abc123"]
        })

        assert valid

    def test_valid_external_config(self):
        """Test valid external link config."""
        valid, issues = ExternalLinkRule.validate_config({
            "mode": "blacklist",
            "blacklist": ["bad.com"]
        })

        assert valid

    def test_invalid_mode(self):
        """Test invalid mode fails."""
        valid, issues = ExternalLinkRule.validate_config({
            "mode": "invalid"
        })

        assert not valid
