"""Regression tests for poll question/option count and length boundaries.

The deployment config is untrusted input: a widened or malformed value must
never turn a bounded poll into an unbounded one. These tests pin the hard
envelope in ``src.core.polls.limits`` and verify the creation validators use
it instead of reading config blindly.
"""

import pytest

from src.core.polls.exceptions import (
    PollOptionLimitError,
    PollOptionLimitError,
    PollOptionLimitError,
    PollOptionLimitError,
    PollOptionLimitError,
    PollOptionLimitError,
    InvalidPollOptionError,
    InvalidPollQuestionError,
)
from src.core.polls.limits import (
    bound_option_count,
    bound_option_count,
    bound_option_count,
    bound_option_count,
    bound_option_count,
    bound_option_count,
    MAX_OPTIONS,
    MAX_OPTION_LENGTH,
    MAX_QUESTION_LENGTH,
    MIN_OPTIONS,
    bound_count,
    resolve_field_limits,
    validate_option_text,
    validate_question_text,
)
from src.core.polls.manager.creation import CreationMixin
from src.core.security.hardening import HardeningViolation, bounded_int


class _ConfigHolder:
    """Minimal stand-in exposing only the ``_config`` the validators read."""

    def __init__(self, config):
        self._config = config

    _validate_question = CreationMixin._validate_question
    _validate_option = CreationMixin._validate_option


class TestBoundCountEnvelope:
    @pytest.mark.parametrize(
        "bad", [-1, -(10**9), 10**9, "not-a-number", None, object()]
    )
    def test_out_of_envelope_falls_back_to_default(self, bad):
        assert (
            bound_count(
                bad,
                "field",
                minimum=MIN_OPTIONS,
                maximum=MAX_OPTIONS,
                default=MAX_OPTIONS,
            )
            == MAX_OPTIONS
        )

    def test_in_envelope_value_honoured(self):
        assert (
            bound_count(
                7,
                "field",
                minimum=MIN_OPTIONS,
                maximum=MAX_OPTIONS,
                default=MAX_OPTIONS,
            )
            == 7
        )

    def test_direct_bounded_int_still_rejects(self):
        """The shared primitive remains strict; only our wrapper fails safe."""
        with pytest.raises(HardeningViolation):
            bounded_int(10**9, "field", minimum=0, maximum=10)


class TestResolveFieldLimits:
    def test_defaults_match_envelope(self):
        limits = resolve_field_limits({})
        assert limits.min_options == MIN_OPTIONS
        assert limits.max_options == MAX_OPTIONS
        assert limits.max_question_length == MAX_QUESTION_LENGTH
        assert limits.max_option_length == MAX_OPTION_LENGTH

    @pytest.mark.parametrize("widened", [10**6, -5, "999999999"])
    def test_widened_config_cannot_raise_ceiling(self, widened):
        limits = resolve_field_limits(
            {
                "max_options": widened,
                "max_question_length": widened,
                "max_option_length": widened,
            }
        )
        assert limits.max_options <= MAX_OPTIONS
        assert limits.max_question_length <= MAX_QUESTION_LENGTH
        assert limits.max_option_length <= MAX_OPTION_LENGTH

    def test_tighter_config_is_honoured(self):
        limits = resolve_field_limits(
            {"max_options": 4, "max_question_length": 80, "max_option_length": 30}
        )
        assert limits.max_options == 4
        assert limits.max_question_length == 80
        assert limits.max_option_length == 30

    def test_inverted_option_counts_collapse(self):
        limits = resolve_field_limits({"min_options": 10, "max_options": 2})
        assert limits.min_options == 2
        assert limits.max_options == 2


class TestCreationValidatorsUseEnvelope:
    def test_huge_configured_question_limit_still_rejects_oversize(self):
        holder = _ConfigHolder({"max_question_length": 10**9})
        with pytest.raises(InvalidPollQuestionError):
            holder._validate_question("x" * (MAX_QUESTION_LENGTH + 1))
        assert holder._validate_question("x" * MAX_QUESTION_LENGTH)

    def test_huge_configured_option_limit_still_rejects_oversize(self):
        holder = _ConfigHolder({"max_option_length": 10**9})
        with pytest.raises(InvalidPollOptionError):
            holder._validate_option("x" * (MAX_OPTION_LENGTH + 1))
        assert holder._validate_option("x" * MAX_OPTION_LENGTH)

    def test_blank_question_and_option_rejected(self):
        holder = _ConfigHolder({})
        with pytest.raises(InvalidPollQuestionError):
            holder._validate_question("   ")
        with pytest.raises(InvalidPollOptionError):
            holder._validate_option("   ")


class TestSharedFieldValidators:
    """The shared helpers enforce the envelope without a manager instance."""

    def test_question_text_is_stripped_and_bounded(self):
        assert validate_question_text("  Why?  ", {}) == "Why?"
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), {})

    def test_option_text_is_stripped_and_bounded(self):
        assert validate_option_text("  Yes  ", {}) == "Yes"
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), {})

    def test_widened_config_cannot_expand_question_or_option(self):
        hostile = {"max_question_length": 10**9, "max_option_length": 10**9}
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), hostile)
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), hostile)

    def test_option_count_is_bounded_even_with_widened_config(self):
        assert bound_option_count(["A", "B"], {}) == 2
        with pytest.raises(PollOptionLimitError):
            bound_option_count(["x"] * (MAX_OPTIONS + 1), {"max_options": 10**9})
        with pytest.raises(PollOptionLimitError):
            bound_option_count([], {"min_options": -5})


class TestSharedFieldValidators:
    """The shared helpers enforce the envelope without a manager instance."""

    def test_question_text_is_stripped_and_bounded(self):
        assert validate_question_text("  Why?  ", {}) == "Why?"
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), {})

    def test_option_text_is_stripped_and_bounded(self):
        assert validate_option_text("  Yes  ", {}) == "Yes"
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), {})

    def test_widened_config_cannot_expand_question_or_option(self):
        hostile = {"max_question_length": 10**9, "max_option_length": 10**9}
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), hostile)
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), hostile)

    def test_option_count_is_bounded_even_with_widened_config(self):
        assert bound_option_count(["A", "B"], {}) == 2
        with pytest.raises(PollOptionLimitError):
            bound_option_count(["x"] * (MAX_OPTIONS + 1), {"max_options": 10**9})
        with pytest.raises(PollOptionLimitError):
            bound_option_count([], {"min_options": -5})


class TestSharedFieldValidators:
    """The shared helpers enforce the envelope without a manager instance."""

    def test_question_text_is_stripped_and_bounded(self):
        assert validate_question_text("  Why?  ", {}) == "Why?"
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), {})

    def test_option_text_is_stripped_and_bounded(self):
        assert validate_option_text("  Yes  ", {}) == "Yes"
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), {})

    def test_widened_config_cannot_expand_question_or_option(self):
        hostile = {"max_question_length": 10**9, "max_option_length": 10**9}
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), hostile)
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), hostile)

    def test_option_count_is_bounded_even_with_widened_config(self):
        assert bound_option_count(["A", "B"], {}) == 2
        with pytest.raises(PollOptionLimitError):
            bound_option_count(["x"] * (MAX_OPTIONS + 1), {"max_options": 10**9})
        with pytest.raises(PollOptionLimitError):
            bound_option_count([], {"min_options": -5})


class TestSharedFieldValidators:
    """The shared helpers enforce the envelope without a manager instance."""

    def test_question_text_is_stripped_and_bounded(self):
        assert validate_question_text("  Why?  ", {}) == "Why?"
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), {})

    def test_option_text_is_stripped_and_bounded(self):
        assert validate_option_text("  Yes  ", {}) == "Yes"
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), {})

    def test_widened_config_cannot_expand_question_or_option(self):
        hostile = {"max_question_length": 10**9, "max_option_length": 10**9}
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), hostile)
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), hostile)

    def test_option_count_is_bounded_even_with_widened_config(self):
        assert bound_option_count(["A", "B"], {}) == 2
        with pytest.raises(PollOptionLimitError):
            bound_option_count(["x"] * (MAX_OPTIONS + 1), {"max_options": 10**9})
        with pytest.raises(PollOptionLimitError):
            bound_option_count([], {"min_options": -5})


class TestSharedFieldValidators:
    """The shared helpers enforce the envelope without a manager instance."""

    def test_question_text_is_stripped_and_bounded(self):
        assert validate_question_text("  Why?  ", {}) == "Why?"
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), {})

    def test_option_text_is_stripped_and_bounded(self):
        assert validate_option_text("  Yes  ", {}) == "Yes"
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), {})

    def test_widened_config_cannot_expand_question_or_option(self):
        hostile = {"max_question_length": 10**9, "max_option_length": 10**9}
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), hostile)
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), hostile)

    def test_option_count_is_bounded_even_with_widened_config(self):
        assert bound_option_count(["A", "B"], {}) == 2
        with pytest.raises(PollOptionLimitError):
            bound_option_count(["x"] * (MAX_OPTIONS + 1), {"max_options": 10**9})
        with pytest.raises(PollOptionLimitError):
            bound_option_count([], {"min_options": -5})


class TestSharedFieldValidators:
    """The shared helpers enforce the envelope without a manager instance."""

    def test_question_text_is_stripped_and_bounded(self):
        assert validate_question_text("  Why?  ", {}) == "Why?"
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), {})

    def test_option_text_is_stripped_and_bounded(self):
        assert validate_option_text("  Yes  ", {}) == "Yes"
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), {})

    def test_widened_config_cannot_expand_question_or_option(self):
        hostile = {"max_question_length": 10**9, "max_option_length": 10**9}
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), hostile)
        with pytest.raises(InvalidPollOptionError):
            validate_option_text("x" * (MAX_OPTION_LENGTH + 1), hostile)

    def test_option_count_is_bounded_even_with_widened_config(self):
        assert bound_option_count(["A", "B"], {}) == 2
        with pytest.raises(PollOptionLimitError):
            bound_option_count(["x"] * (MAX_OPTIONS + 1), {"max_options": 10**9})
        with pytest.raises(PollOptionLimitError):
            bound_option_count([], {"min_options": -5})
