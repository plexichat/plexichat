"""Tests for automod keyword rules."""

import pytest

from src.core.automod.models import RuleType, ActionType
from src.core.automod.exceptions import RuleValidationError


@pytest.mark.automod
class TestKeywordRules:
    """Tests for keyword-based automod rules."""

    def test_create_keyword_rule(self, automod_manager, test_server):
        """Test creating a keyword rule."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="No Bad Words",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["badword", "spam"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "delete_message"}],
        )
        assert rule.name == "No Bad Words"
        assert rule.rule_type == RuleType.KEYWORD
        assert rule.enabled is True
        assert len(rule.actions) == 1
        assert rule.actions[0].action_type == ActionType.DELETE_MESSAGE

    def test_keyword_rule_matches_content(self, automod_manager, test_server):
        """Test that keyword rule detects matching content."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Keyword Match",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["badword"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1",
            (server.id,),
        )
        channel_id = channel["id"] if channel else 0

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=99999,
            content="This contains badword here",
        )
        assert not result.passed
        assert len(result.violations) > 0

    def test_keyword_rule_no_match(self, automod_manager, test_server):
        """Test that keyword rule does not match clean content."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="No Match",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["badword"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1",
            (server.id,),
        )
        channel_id = channel["id"] if channel else 0

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=99999,
            content="This is totally fine content",
        )
        assert result.passed

    def test_keyword_rule_case_insensitive(self, automod_manager, test_server):
        """Test case-insensitive matching."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Case Insensitive",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["badword"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1",
            (server.id,),
        )
        channel_id = channel["id"] if channel else 0

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=99999,
            content="BADWORD in caps",
        )
        assert not result.passed

    def test_keyword_rule_whole_word(self, automod_manager, test_server):
        """Test whole-word matching doesn't match substrings."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Whole Word",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["bad"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1",
            (server.id,),
        )
        channel_id = channel["id"] if channel else 0

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=99999,
            content="This is a badword substring",
        )
        assert result.passed  # "bad" should not match inside "badword"

    def test_keyword_rule_invalid_config(self, automod_manager, test_server):
        """Test that invalid keyword config raises validation error."""
        server, owner = test_server
        with pytest.raises(RuleValidationError):
            automod_manager.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="Invalid",
                rule_type=RuleType.KEYWORD,
                rule_config={},  # Missing required 'keywords'
                actions=[{"action_type": "log_only"}],
            )
