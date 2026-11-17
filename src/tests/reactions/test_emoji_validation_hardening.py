"""Regression tests for reaction emoji validator hardening.

The custom-emoji branch used to call ``int()`` on an unbounded digit string
before any length/range check. A token with thousands of digits crashed with
an uncaught ValueError (Python's 4300-digit integer conversion cap) instead of
raising InvalidEmojiError, and huge ids outside the snowflake range reached
SQLite. Both are rejected cleanly now.
"""

import pytest

from src.core.reactions import InvalidEmojiError
from src.core.reactions.manager.validation import ReactionValidationMixin


@pytest.fixture
def validator():
    # _validate_emoji only uses pure validation helpers; no DB needed.
    return ReactionValidationMixin.__new__(ReactionValidationMixin)


def test_oversized_custom_emoji_id_is_validation_error(validator):
    """A >4300-digit id must not escape as an uncaught ValueError (DoS)."""
    token = "<:x:" + "9" * 5000 + ">"
    assert len(token) > 4300
    with pytest.raises(InvalidEmojiError):
        validator._validate_emoji(token)


def test_out_of_range_custom_emoji_id_rejected(validator):
    """Ids beyond the snowflake range must not flow into SQLite/DB events."""
    with pytest.raises(InvalidEmojiError):
        validator._validate_emoji("<:x:999999999999999999999999999999>")


def test_zero_and_negative_custom_emoji_id_rejected(validator):
    with pytest.raises(InvalidEmojiError):
        validator._validate_emoji("<:x:0>")
    with pytest.raises(InvalidEmojiError):
        validator._validate_emoji("<:x:-1>")


def test_oversized_custom_emoji_name_rejected(validator):
    with pytest.raises(InvalidEmojiError):
        validator._validate_emoji("<:" + "a" * 5000 + ":123>")


@pytest.mark.parametrize(
    "emoji",
    ["<:party:123456789>", "<a:dance:987654321>", "<:preserve_id:123456789012345678>"],
)
def test_legitimate_custom_emoji_still_accepted(validator, emoji):
    is_custom, emoji_id, stored = validator._validate_emoji(emoji)
    assert is_custom is True
    assert emoji_id > 0
    assert stored == emoji
