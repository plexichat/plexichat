"""Regression tests for the mass-emoji automod rule.

``MassEmojiRule.UNICODE_EMOJI_PATTERN`` is a character class with a ``+``
quantifier, so ``findall`` returns *runs* of adjacent emoji rather than one
match per emoji. Counting the matches therefore collapsed ``"😀" * 20`` to a
single emoji and let mass-emoji spam through whenever the emoji-percentage
path did not also trip. These pin the per-codepoint count.
"""

from src.core.automod.models import (
    Rule,
    RuleAction,
    ActionType,
    RuleType,
)
from src.core.automod.rules.emoji import MassEmojiRule


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="t",
        rule_type=RuleType.MASS_EMOJI,
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


def _emoji_rule(**config):
    base = {"max_emoji": 10, "max_percentage": 50.0, "min_length": 5}
    base.update(config)
    return MassEmojiRule(_rule(base))


class TestUnicodeEmojiCounting:
    def test_adjacent_emoji_are_counted_individually(self):
        rule = _emoji_rule()
        # A long run of adjacent emoji must not be collapsed to one emoji;
        # the surrounding text keeps the emoji *percentage* below the limit so
        # only the absolute count can trip the rule.
        content = (
            "hello world " + "\U0001f600" * 20 + " and some more words here to keep the"
            " percentage low"
        )
        match = rule.check(content, 1, 1)
        assert match.matched
        assert match.match_details["unicode_emoji"] == 20
        assert match.match_details["total_emoji"] == 20

    def test_run_below_threshold_still_passes(self):
        rule = _emoji_rule()
        match = rule.check("hello " + "\U0001f600" * 5 + " world", 1, 1)
        assert not match.matched

    def test_each_adjacent_emoji_added_pushes_over_the_limit(self):
        rule = _emoji_rule(max_emoji=10)
        filler = " words to keep the percentage low enough that only the count trips"
        below = "hello " + "\U0001f600" * 10 + filler
        above = "hello " + "\U0001f600" * 11 + filler
        assert not rule.check(below, 1, 1).matched
        match = rule.check(above, 1, 1)
        assert match.matched
        assert match.match_details["unicode_emoji"] == 11


class TestCustomAndUnicodeCounting:
    def test_custom_and_unicode_counts_are_summed(self):
        rule = _emoji_rule(max_emoji=10)
        content = (
            "<:party:1234567890>" * 6 + "\U0001f600" * 6 + " ordinary words follow here"
        )
        match = rule.check(content, 1, 1)
        assert match.matched
        assert match.match_details["custom_emoji"] == 6
        assert match.match_details["unicode_emoji"] == 6
        assert match.match_details["total_emoji"] == 12

    def test_counting_can_be_disabled_per_kind(self):
        rule = _emoji_rule(count_unicode=False, max_emoji=1)
        content = "\U0001f600" * 20 + " ordinary words follow here"
        assert not rule.check(content, 1, 1).matched
