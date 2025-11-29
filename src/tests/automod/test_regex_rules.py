"""
Tests for regex pattern rules.
"""

import pytest

from src.core import automod
from src.core.automod import RuleType
from src.core.automod.rules.regex import RegexRule


@pytest.mark.automod
class TestRegexRule:
    """Tests for RegexRule."""
    
    def test_matches_pattern(self, regex_rule):
        """Test regex pattern matching."""
        rule, server, channel, owner = regex_rule
        
        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Get free money now!"
        )
        
        assert not result.passed
        assert result.violations[0].rule_type == RuleType.REGEX
    
    def test_matches_credit_card_pattern(self, regex_rule):
        """Test credit card number pattern."""
        rule, server, channel, owner = regex_rule
        
        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="My card is 1234567890123456"
        )
        
        assert not result.passed
    
    def test_no_match_clean_content(self, regex_rule):
        """Test clean content passes."""
        rule, server, channel, owner = regex_rule
        
        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="This is a normal message"
        )
        
        assert result.passed
    
    def test_case_insensitive_by_default(self, regex_rule):
        """Test patterns are case insensitive by default."""
        rule, server, channel, owner = regex_rule
        
        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Get FREE MONEY now!"
        )
        
        assert not result.passed


@pytest.mark.automod
class TestRegexRuleValidation:
    """Tests for regex rule config validation."""
    
    def test_valid_config(self):
        """Test valid configuration passes."""
        valid, issues = RegexRule.validate_config({
            "patterns": [
                {"pattern": r"\btest\b", "name": "test_pattern"}
            ]
        })
        
        assert valid
        assert len(issues) == 0
    
    def test_missing_patterns(self):
        """Test missing patterns fails validation."""
        valid, issues = RegexRule.validate_config({})
        
        assert not valid
        assert any("patterns" in issue for issue in issues)
    
    def test_invalid_regex(self):
        """Test invalid regex fails validation."""
        valid, issues = RegexRule.validate_config({
            "patterns": [
                {"pattern": r"[invalid(regex", "name": "bad"}
            ]
        })
        
        assert not valid
        assert any("invalid regex" in issue for issue in issues)
    
    def test_missing_pattern_field(self):
        """Test pattern without pattern field fails."""
        valid, issues = RegexRule.validate_config({
            "patterns": [
                {"name": "no_pattern"}
            ]
        })
        
        assert not valid
