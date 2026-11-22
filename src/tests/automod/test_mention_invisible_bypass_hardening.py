"""Regression: invisible Unicode must not hide mention spam from AutoMod.

``MentionSpamRule`` used to run its ``<@id>`` / ``@everyone`` patterns on the
raw message. A visually undetectable format/control character spliced *inside*
a token (``<@123\u200b456>``, ``@e\u200bveryone``) broke the match while the
reader saw an ordinary mention, letting mass-mention and @everyone abuse pass
the only rule that counts mentions. Sibling rules (repeated, emoji, keyword,
spam, links) already fold these characters away; this rule was the remaining
gap.

Every regex here is linear (``\\d+`` / literal alternation), so the fix is a
canonicalization gap, not a ReDoS. The payloads below are the exact obfuscated
forms that produced zero matches before the fix.
"""

import pytest

from src.core.automod.models import (
    ActionType,
    Rule,
    RuleAction,
    RuleType,
)
from src.core.automod.rules.mentions import MentionSpamRule


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="mention test",
        rule_type=RuleType.MENTION_SPAM,
        enabled=True,
        config=config,
        actions=[RuleAction(action_type=ActionType.DELETE_MESSAGE)],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=0,
    )


# Cf/Cc separators that render nothing but used to split a mention token.
SEPARATORS = [
    "\u200b",  # zero-width space
    "\u200d",  # zero-width joiner
    "\u00ad",  # soft hyphen
    "\u2060",  # word joiner
    "\ufeff",  # BOM
    "\u202e",  # right-to-left override
    "\u200e",  # left-to-right mark
    "\x00",  # NUL (Cc)
]


def _mentions(count, separator=""):
    return " ".join(
        "<@" + separator.join(str(100000000000000000 + i)) + ">" for i in range(count)
    )


def test_plain_mentions_are_counted_control():
    match = MentionSpamRule(
        _rule({"max_user_mentions": 2, "max_total_mentions": 3})
    ).check(_mentions(6), 1, 1)
    assert match.matched
    assert match.match_details["user_mentions"] == 6


@pytest.mark.parametrize("separator", SEPARATORS)
def test_invisible_separators_do_not_hide_user_mentions(separator):
    # Plain form is detected; before the fix every obfuscated form reported 0
    # user mentions and returned no match.
    match = MentionSpamRule(
        _rule({"max_user_mentions": 2, "max_total_mentions": 3})
    ).check(_mentions(6, separator), 1, 1)
    assert match.matched, repr(separator)
    assert match.match_details["user_mentions"] == 6, repr(separator)


def test_fullwidth_digit_mentions_are_still_counted():
    # NFKC folds fullwidth compatibility digits onto their ASCII forms.
    mentions = " ".join(
        "<@" + "".join(chr(0xFF10 + int(d)) for d in str(100000000000000000 + i)) + ">"
        for i in range(6)
    )
    match = MentionSpamRule(
        _rule({"max_user_mentions": 2, "max_total_mentions": 3})
    ).check(mentions, 1, 1)
    assert match.matched
    assert match.match_details["user_mentions"] == 6


@pytest.mark.parametrize("separator", SEPARATORS)
def test_invisible_separators_do_not_hide_everyone_mentions(separator):
    rule = MentionSpamRule(_rule({"max_total_mentions": 100, "block_everyone": True}))
    # @everyone/@here were the other silent bypass: "@e<ZWSP>veryone" did not
    # match the EVERYONE_PATTERN at all.
    payload = " ".join(
        f"@e{separator}veryone" if i % 2 == 0 else f"@h{separator}ere" for i in range(6)
    )
    match = rule.check(payload, 1, 1)
    assert match.matched, repr(separator)
    assert match.match_details["everyone_mentions"] == 6, repr(separator)
    assert any("everyone" in v for v in match.match_details["violations"])


def test_count_unique_only_operates_on_canonical_mentions():
    rule = MentionSpamRule(
        _rule(
            {
                "max_user_mentions": 1,
                "max_total_mentions": 1,
                "count_unique_only": True,
            }
        )
    )
    # Same user repeated with different invisible splices folds to one target.
    same = " ".join("<@" + "\u200b".join("123456789012345678") + ">" for _ in range(5))
    assert not rule.check(same, 1, 1).matched
    # Two genuinely distinct targets still exceed the unique cap.
    distinct = same + " <@876543210987654321>"
    assert rule.check(distinct, 1, 1).matched


def test_clean_text_is_not_flagged():
    rule = MentionSpamRule(_rule({"max_user_mentions": 2, "max_total_mentions": 3}))
    assert not rule.check("hello world, no mentions here", 1, 1).matched
    assert not rule.check("<@1> and <@2> only", 1, 1).matched
