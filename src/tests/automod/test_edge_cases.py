"""Tests for automod edge cases and boundary conditions."""

import pytest

from src.core.automod.models import RuleType, ActionType, ViolationSeverity, RuleMatch
from src.core.automod.exceptions import RuleValidationError


@pytest.mark.automod
class TestEdgeCases:
    """Tests for automod edge cases and boundary conditions."""

    def test_rule_with_empty_keywords_rejected(self, automod_manager, test_server):
        """Test that keyword rule with empty keywords list is rejected."""
        server, owner = test_server
        with pytest.raises(RuleValidationError):
            automod_manager.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="Empty Keywords",
                rule_type=RuleType.KEYWORD,
                rule_config={
                    "keywords": [],
                    "case_sensitive": False,
                    "whole_word": True,
                },
                actions=[{"action_type": "log_only"}],
            )

    def test_rule_with_no_actions_rejected(self, automod_manager, test_server):
        """Test that rule with no actions is rejected."""
        server, owner = test_server
        with pytest.raises(RuleValidationError):
            automod_manager.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="No Actions",
                rule_type=RuleType.KEYWORD,
                rule_config={
                    "keywords": ["test"],
                    "case_sensitive": False,
                    "whole_word": True,
                },
                actions=[],
            )

    def test_rule_with_empty_name_rejected(self, automod_manager, test_server):
        """Test that rule with empty name is rejected."""
        server, owner = test_server
        with pytest.raises(RuleValidationError):
            automod_manager.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="",
                rule_type=RuleType.KEYWORD,
                rule_config={
                    "keywords": ["test"],
                    "case_sensitive": False,
                    "whole_word": True,
                },
                actions=[{"action_type": "log_only"}],
            )

    def test_case_sensitive_keyword_matching(self, automod_manager, test_server):
        """Test case-sensitive vs case-insensitive keyword matching."""
        server, owner = test_server
        # Case-insensitive rule
        rule_ci = automod_manager.create_rule(
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
        assert rule_ci is not None

    def test_multiple_rules_on_same_server(self, automod_manager, test_server):
        """Test creating multiple rules on the same server."""
        server, owner = test_server
        rule1 = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rule 1",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["word1"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )
        rule2 = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rule 2",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["word2"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "delete_message"}],
        )
        assert rule1.id != rule2.id

    def test_get_server_rules_returns_all(self, automod_manager, test_server):
        """Test getting all rules for a server."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rule A",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["a"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rule B",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["b"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "delete_message"}],
        )
        rules = automod_manager.get_server_rules(server.id)
        assert len(rules) >= 2

    def test_delete_rule_removes_from_list(self, automod_manager, test_server):
        """Test that deleting a rule removes it from server rules list."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="To Delete",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["deleteme"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )
        count_before = len(automod_manager.get_server_rules(server.id))
        automod_manager.delete_rule(owner.id, rule.id)
        count_after = len(automod_manager.get_server_rules(server.id))
        assert count_after == count_before - 1

    def test_rule_with_multiple_actions(self, automod_manager, test_server):
        """Test creating a rule with multiple actions."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Multi Action",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["bad"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[
                {"action_type": "delete_message"},
                {"action_type": "alert_moderators"},
                {"action_type": "log_only"},
            ],
        )
        assert len(rule.actions) == 3
