"""Regression tests for user-supplied blocked words in the content filter.

Blocked words are attacker-controlled (they arrive from the user's own filter
settings), and the filter used to feed them straight into ``re.sub`` as a
replacement *template*. These tests pin the three ways that broke.
"""

import pytest

from src.core.messaging.content import get_processor
from src.core.messaging.models import ContentFilter, FilterAction


def _filter(words, action):
    return ContentFilter(user_id=1, custom_blocked_words=words, filter_action=action)


@pytest.mark.messaging
class TestBlockedWordReplacementTemplates:
    """A blocked word must never be parsed as a regex replacement template."""

    @pytest.mark.parametrize(
        "word",
        [
            r"\1",  # invalid group reference -> re.PatternError
            r"\2",
            r"x\y",  # invalid escape -> re.PatternError
            r"\g<0>",  # valid template -> silently substituted
            r"\g<name>",
            "\\",  # trailing lone backslash
        ],
    )
    def test_backslash_word_does_not_raise(self, word):
        """The filter is exercised directly: validate_content HTML-escapes
        ``<``/``>`` upstream, so it would never hand those words through."""
        processor = get_processor()
        content = f"before {word} after"
        result = processor._apply_user_filters(
            content, _filter([word], FilterAction.SPOILER)
        )
        assert result == (f"before ||{word}|| after", [word])

    @pytest.mark.parametrize("word", [r"\1", r"x\y"])
    def test_backslash_word_survives_full_validation(self, word):
        processor = get_processor()
        result = processor.validate_content(
            f"before {word} after", user_filter=_filter([word], FilterAction.SPOILER)
        )
        assert f"||{word}||" in result.sanitized_content
        assert result.filtered_words == [word]

    def test_backslash_word_censored(self):
        processor = get_processor()
        result = processor.validate_content(
            r"aa \1 bb", user_filter=_filter([r"\1"], FilterAction.CENSOR)
        )
        assert result.sanitized_content == r"aa ** bb"


@pytest.mark.messaging
class TestBlockedWordCasePreservation:
    """The matched text is substituted, not the configured word."""

    def test_spoiler_preserves_original_casing(self):
        processor = get_processor()
        result = processor.validate_content(
            "I saw SECRET and Secret here",
            user_filter=_filter(["secret"], FilterAction.SPOILER),
        )
        assert result.sanitized_content == "I saw ||SECRET|| and ||Secret|| here"

    def test_censor_preserves_match_length(self):
        processor = get_processor()
        result = processor.validate_content(
            "say SECRET now", user_filter=_filter(["secret"], FilterAction.CENSOR)
        )
        assert result.sanitized_content == "say ****** now"


@pytest.mark.messaging
class TestBlockedWordOverlap:
    """Overlapping words must not re-match text the filter just rewrote."""

    def test_overlapping_words_do_not_nest_spoiler_markers(self):
        processor = get_processor()
        result = processor.validate_content(
            "a badword b", user_filter=_filter(["bad", "badword"], FilterAction.SPOILER)
        )
        assert result.sanitized_content == "a ||badword|| b"
        assert result.filtered_words == ["badword"]

    def test_overlap_result_is_order_independent(self):
        """Blocked words arrive in a set; the output must not vary."""
        processor = get_processor()
        first = processor.validate_content(
            "a badword b",
            user_filter=_filter(["bad", "badword"], FilterAction.SPOILER),
        )
        second = processor.validate_content(
            "a badword b",
            user_filter=_filter(["badword", "bad"], FilterAction.SPOILER),
        )
        assert first.sanitized_content == second.sanitized_content
        assert first.filtered_words == second.filtered_words

    def test_duplicate_words_reported_once(self):
        processor = get_processor()
        result = processor.validate_content(
            "secret SECRET",
            user_filter=_filter(["secret", "secret"], FilterAction.CENSOR),
        )
        assert result.sanitized_content == "****** ******"
        assert result.filtered_words == ["secret"]

    def test_unmatched_word_is_not_reported(self):
        processor = get_processor()
        result = processor.validate_content(
            "nothing to see", user_filter=_filter(["secret"], FilterAction.CENSOR)
        )
        assert result.sanitized_content == "nothing to see"
        assert result.filtered_words == []

    def test_empty_word_list_is_a_no_op(self):
        processor = get_processor()
        result = processor.validate_content(
            "untouched", user_filter=_filter([], FilterAction.CENSOR)
        )
        assert result.sanitized_content == "untouched"
        assert result.filtered_words == []
