"""
Tests for keyword filter rules.
"""

import pytest

from src.core import automod
from src.core.automod import RuleType
from src.core.automod.rules.keyword import KeywordRule


@pytest.mark.automod
class TestKeywordRule:
    """Tests for KeywordRule."""

    def test_matches_exact_keyword(self, keyword_rule):
        """Test that exact keywords are matched."""
        rule, server, channel, owner = keyword_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="This contains badword in it"
        )

        assert not result.passed
        assert len(result.violations) == 1
        assert result.violations[0].rule_type == RuleType.KEYWORD

    def test_case_insensitive_match(self, keyword_rule):
        """Test case insensitive matching."""
        rule, server, channel, owner = keyword_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="This contains BADWORD in uppercase"
        )

        assert not result.passed

    def test_whole_word_boundary(self, keyword_rule):
        """Test whole word matching respects boundaries."""
        rule, server, channel, owner = keyword_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="This is badwording around"
        )

        assert result.passed

    def test_no_match_clean_content(self, keyword_rule):
        """Test clean content passes."""
        rule, server, channel, owner = keyword_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="This is a perfectly clean message"
        )

        assert result.passed
        assert len(result.violations) == 0

    def test_multiple_keywords_matched(self, automod_module, test_server_for_automod):
        """Test multiple keywords in same message."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Multi Keyword Test",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["word1", "word2", "word3"],
                "case_sensitive": False,
                "whole_word": True
            },
            actions=[{"action_type": "log_only"}]
        )

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Message with word1 and word2 together"
        )

        assert not result.passed
        assert result.violations[0].matched_content is not None
        assert "word1" in result.violations[0].matched_content
        assert "word2" in result.violations[0].matched_content


@pytest.mark.automod
class TestKeywordRuleValidation:
    """Tests for keyword rule config validation."""

    def test_valid_config(self):
        """Test valid configuration passes."""
        valid, issues = KeywordRule.validate_config({
            "keywords": ["test", "word"],
            "case_sensitive": False,
            "whole_word": True
        })

        assert valid
        assert len(issues) == 0

    def test_missing_keywords(self):
        """Test missing keywords fails validation."""
        valid, issues = KeywordRule.validate_config({
            "case_sensitive": False
        })

        assert not valid
        assert any("keywords" in issue for issue in issues)

    def test_invalid_keywords_type(self):
        """Test non-list keywords fails validation."""
        valid, issues = KeywordRule.validate_config({
            "keywords": "not a list"
        })

        assert not valid

    def test_invalid_case_sensitive_type(self):
        """Test non-boolean case_sensitive fails."""
        valid, issues = KeywordRule.validate_config({
            "keywords": ["test"],
            "case_sensitive": "yes"
        })

        assert not valid
