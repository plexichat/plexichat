"""Regression: invisible Unicode separators must not defeat spam rules.

``RepeatedCharsRule`` and ``MassEmojiRule`` used to inspect the raw message
text. A visually undetectable format character (U+200B zero-width space, U+00AD
soft hyphen, U+2060 word joiner, a bidi override, a BOM, ...) spliced between
repeated characters broke the ``(.)\\1{n,}`` run, and between emoji it padded
the non-emoji text length so the emoji-percentage check stayed under its cap.
The rendered message is identical to the reader, so this was a clean evasion
of both anti-spam controls.

Both rules now NFKC-fold and strip invisible format/control characters before
matching, mirroring the keyword/duplicate/link moderation layers.
"""

import pytest

from src.core.automod.models import (
    Rule,
    RuleAction,
    ActionType,
    RuleType,
)
from src.core.automod.rules.emoji import MassEmojiRule
from src.core.automod.rules.repeated import RepeatedCharsRule


def _rule(rule_type, config):
    return Rule(
        id=1,
        server_id=1,
        name="t",
        rule_type=rule_type,
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


def _repeated_rule():
    return RepeatedCharsRule(
        _rule(
            RuleType.REPEATED_CHARS,
            {"max_repeats": 5, "min_occurrences": 3},
        )
    )


def _emoji_rule():
    # High absolute cap so only the percentage path can trip; invisible
    # padding used to keep the computed percentage at/below the 50% ceiling.
    return MassEmojiRule(
        _rule(
            RuleType.MASS_EMOJI,
            {"max_emoji": 100, "max_percentage": 50.0, "min_length": 5},
        )
    )


@pytest.mark.parametrize(
    "separator",
    [
        "",  # control: the plain form was already detected
        "\u200b",  # zero-width space
        "\u00ad",  # soft hyphen
        "\u2060",  # word joiner
        "\ufeff",  # BOM
        "\u202e",  # right-to-left override
    ],
)
def test_repeated_chars_detected_through_invisible_separators(separator):
    content = "hello" + separator.join(["o"] * 9)
    match = _repeated_rule().check(content, 1, 1)
    assert match.matched, repr(separator)
    assert match.match_details["occurrence_count"] >= 3


def test_repeated_chars_detected_through_fullwidth_lookalikes():
    # NFKC folds fullwidth Latin letters onto their ASCII forms.
    match = _repeated_rule().check("hello" + "\uff4f" * 9, 1, 1)
    assert match.matched


def test_repeated_chars_clean_text_is_not_flagged():
    rule = _repeated_rule()
    assert not rule.check("hello world", 1, 1).matched
    assert not rule.check("a perfectly normal sentence", 1, 1).matched


def test_mass_emoji_percentage_not_deflated_by_invisible_padding():
    rule = _emoji_rule()
    emoji = "\U0001f600"
    # Without folding, the ZWSPs counted as non-emoji text so the computed
    # emoji percentage dropped to 50% and the wall of emoji passed.
    for separator in ("", "\u200b", "\ufeff"):
        content = separator.join([emoji] * 20)
        match = rule.check(content, 1, 1)
        assert match.matched, repr(separator)
        assert match.match_details["total_emoji"] == 20
        assert match.match_details["text_length"] == 0


def test_mass_emoji_clean_text_is_not_flagged():
    assert not _emoji_rule().check("hello world", 1, 1).matched
