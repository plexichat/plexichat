"""Regression for the unicode-emoji validator hot path.

``_is_valid_unicode_emoji`` is called on every add/remove reaction.  Its lookup
tables used to be rebuilt inside the method (a list of 8 tuples plus two sets)
and every character allocated a fresh ``any(generator)`` object.  They are now
immutable module-level constants and the range scan is an inline loop.

The tests below lock in validation behaviour (so a future refactor cannot
silently change which tokens are accepted) and guard the structural property
that keeps the hot path allocation-free.
"""

import pytest

from src.core.reactions import InvalidEmojiError
from src.core.reactions.manager import validation as validation_module
from src.core.reactions.manager.validation import ReactionValidationMixin


@pytest.fixture
def validator():
    return ReactionValidationMixin.__new__(ReactionValidationMixin)


# (input, expected validity) for the pure unicode-emoji predicate.
_CASES = [
    ("😀", True),  # U+1F600 in 1F000-1FAFF
    ("👍", True),  # U+1F44D
    ("❤️", True),  # U+2764 + VS16 (FE0F)
    ("👨‍👩‍👧‍👦", True),  # 3 ZWJ (allowed)
    ("👨‍👩‍👧‍👦‍👨", False),  # 4 ZWJ (rejected)
    ("1️⃣", True),  # keycap sequence
    ("#️⃣", True),
    ("©", True),  # singleton
    ("®", True),  # singleton
    ("\u20e3", True),  # singleton combining enclosing keycap
    ("\u200b", False),  # rejected zero-width space
    ("\u202e", False),  # rejected bidi override
    ("\ufeff", False),  # rejected BOM
    ("hello", False),  # plain ASCII word
    ("abc_123", False),
    ("a", False),
    ("\x01", False),  # control char
    ("😀a", False),  # emoji followed by ASCII letter
    ("", False),
    (":thumbsup:", False),  # shortcodes are handled one level up
]


@pytest.mark.parametrize("text,expected", _CASES)
def test_unicode_predicate_behaviour_preserved(validator, text, expected):
    assert validator._is_valid_unicode_emoji(text) is expected


def test_validate_emoji_end_to_end(validator):
    is_custom, custom_id, stored = validator._validate_emoji("😀")
    assert (is_custom, custom_id, stored) == (False, None, "😀")

    is_custom, custom_id, stored = validator._validate_emoji(":heart:")
    assert (is_custom, custom_id, stored) == (False, None, ":heart:")

    is_custom, custom_id, stored = validator._validate_emoji("<:party:123456789>")
    assert (is_custom, custom_id, stored) == (True, 123456789, "<:party:123456789>")

    with pytest.raises(InvalidEmojiError):
        validator._validate_emoji("hello")


def test_hotpath_tables_are_module_level_and_immutable():
    """Guard against re-introducing per-call table construction on the hot path."""
    assert isinstance(validation_module.VALID_EMOJI_RANGES, tuple)
    assert isinstance(validation_module.SINGLETON_CHARS, frozenset)
    assert isinstance(validation_module.REJECTED_CHARS, frozenset)
    assert validation_module.ASCII_WORD_PATTERN.fullmatch("hello") is not None
    assert validation_module.ASCII_WORD_PATTERN.fullmatch("hello😀") is None
