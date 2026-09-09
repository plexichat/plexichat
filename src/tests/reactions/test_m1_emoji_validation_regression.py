"""Regression test for findings Agent 21 [M1]: emoji validator must reject
plain ASCII words (e.g. "HELLO") while still accepting genuine unicode
emoji and server custom-emoji formats.

Exercises ReactionValidationMixin._validate_emoji directly (no DB needed).
"""

import pytest

from src.core.reactions import InvalidEmojiError
from src.core.reactions.manager.validation import ReactionValidationMixin


@pytest.fixture
def validator():
    # Bypass __init__ (needs db/config); _validate_emoji only uses
    # _is_valid_unicode_emoji, which is pure.
    return ReactionValidationMixin.__new__(ReactionValidationMixin)


@pytest.mark.parametrize("junk", ["HELLO", "abc123", "thumbsup", "test123"])
def test_m1_ascii_words_rejected(validator, junk):
    with pytest.raises(InvalidEmojiError):
        validator._validate_emoji(junk)


@pytest.mark.parametrize(
    "emoji",
    [
        "\U0001f44d",
        "\u2764\ufe0f",
        "1\ufe0f\u20e3",
        "\U0001f468\u200d\U0001f469\u200d\U0001f467",
    ],
)
def test_m1_genuine_unicode_emoji_accepted(validator, emoji):
    is_custom, emoji_id, stored = validator._validate_emoji(emoji)
    assert stored == emoji
    assert is_custom is False


@pytest.mark.parametrize(
    "emoji", [":thumbsup:", ":100:", "<:party:123456789>", "<a:dance:987654321>"]
)
def test_m1_custom_emoji_formats_accepted(validator, emoji):
    is_custom, emoji_id, stored = validator._validate_emoji(emoji)
    assert stored == emoji
