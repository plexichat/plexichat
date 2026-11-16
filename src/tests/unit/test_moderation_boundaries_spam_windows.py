"""Spam-window counters and thresholds are finite and bounded."""

import pytest

from src.core.automod.models import Rule, RuleType
from src.core.automod.rules.emoji import MassEmojiRule
from src.core.automod.rules.mentions import MentionSpamRule
from src.core.automod.rules.spam import MessageSpamRule


def _rule(config, rule_type):
    return Rule(
        id=1,
        server_id=2,
        name="r",
        rule_type=rule_type,
        enabled=True,
        config=config,
        actions=[],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=3,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_messages", 10**12),
        ("max_messages", 0),
        ("window_seconds", 10**12),
        ("duplicate_threshold", -1),
        ("duplicate_window_seconds", 10**12),
    ],
)
def test_spam_config_rejects_out_of_range(field, value):
    ok, issues = MessageSpamRule.validate_config({"max_messages": 5, field: value})
    assert not ok and issues


def test_spam_stored_oversized_values_clamped():
    rule = MessageSpamRule(
        _rule({"max_messages": 10**12, "window_seconds": -9}, RuleType.MESSAGE_SPAM)
    )
    assert 1 <= rule._max_messages <= 10_000
    assert 1 <= rule._window_seconds <= 86_400


def test_mention_limits_bounded():
    ok, issues = MentionSpamRule.validate_config({"max_user_mentions": 10**12})
    assert not ok and issues
    rule = MentionSpamRule(_rule({"max_total_mentions": -5}, RuleType.MENTION_SPAM))
    assert 0 <= rule._max_total_mentions <= 10_000


def test_emoji_threshold_bounded():
    ok, issues = MassEmojiRule.validate_config({"max_emoji": 10**12})
    assert not ok and issues
    rule = MassEmojiRule(_rule({"max_emoji": 10**9}, RuleType.MASS_EMOJI))
    assert rule._max_emoji <= 1_000
