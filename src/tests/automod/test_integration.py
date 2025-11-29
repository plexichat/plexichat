"""
Integration tests for automod module.
"""

import pytest

from src.core import automod
from src.core.automod import RuleType, ActionType


@pytest.mark.automod
@pytest.mark.integration
class TestAutoModIntegration:
    """Integration tests for full automod workflow."""
    
    def test_full_violation_workflow(self, automod_module, test_server_for_automod, user_pool):
        """Test complete violation detection and processing."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()
        
        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Integration Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["violation"], "case_sensitive": False, "whole_word": True},
            actions=[
                {"action_type": "delete_message"},
                {"action_type": "alert_moderators"},
                {"action_type": "log_only"}
            ]
        )
        
        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="This contains violation word"
        )
        
        assert not result.passed
        assert len(result.violations) == 1
        assert len(result.actions_to_take) == 3
        
        violation = automod_module.process_violation(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            message_id=None,
            match=result.violations[0],
            actions=result.actions_to_take
        )
        
        assert violation.id is not None
        assert violation.user_id == user.id
        assert violation.rule_id == rule.id
        
        violations = automod_module.get_violations(server.id, user_id=user.id)
        assert len(violations) > 0
        assert violations[0].id == violation.id
        
        reputation = automod_module.get_user_reputation(user.id, server.id)
        assert reputation.score < 100.0
        assert reputation.violation_count > 0
    
    def test_multiple_rules_priority(self, automod_module, test_server_for_automod, user_pool):
        """Test multiple rules with priority ordering."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()
        
        low_priority = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Low Priority",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["test"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "log_only"}],
            priority=1
        )
        
        high_priority = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="High Priority",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["test"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "delete_message"}],
            priority=10
        )
        
        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="test message"
        )
        
        assert not result.passed
        assert result.violations[0].rule_id == high_priority.id
    
    def test_check_all_rules(self, automod_module, test_server_for_automod, user_pool):
        """Test check_all flag continues checking after match."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()
        
        rule1 = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rule 1",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["word1"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "log_only"}],
            check_all=True
        )
        
        rule2 = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rule 2",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["word2"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "log_only"}],
            check_all=True
        )
        
        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="message with word1 and word2"
        )
        
        assert not result.passed
        assert len(result.violations) == 2
    
    def test_rule_enable_disable(self, automod_module, test_server_for_automod, user_pool):
        """Test enabling and disabling rules."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()
        
        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Toggle Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["toggle"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "delete_message"}]
        )
        
        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="toggle test"
        )
        assert not result.passed
        
        automod_module.set_rule_enabled(owner.id, rule.id, False)
        
        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="toggle test"
        )
        assert result.passed
        
        automod_module.set_rule_enabled(owner.id, rule.id, True)
        
        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="toggle test"
        )
        assert not result.passed
    
    def test_bulk_message_scan(self, automod_module, test_server_for_automod, modules, user_pool):
        """Test bulk message scanning for raids."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()
        
        modules.servers.add_member(server.id, user.id)
        
        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Raid Detection",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["raid"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "delete_message"}]
        )
        
        conv = modules.messaging.create_dm(user.id, owner.id)
        
        msg1 = modules.messaging.send_message(user.id, conv.id, "normal message")
        msg2 = modules.messaging.send_message(user.id, conv.id, "raid spam")
        msg3 = modules.messaging.send_message(user.id, conv.id, "another raid")
        
        result = automod_module.scan_messages_bulk(
            server_id=server.id,
            channel_id=channel.id,
            message_ids=[msg1.id, msg2.id, msg3.id]
        )
        
        assert result.total_scanned == 3
        assert result.violations_found == 2
        assert len(result.messages_flagged) == 2
        assert user.id in result.user_violations
