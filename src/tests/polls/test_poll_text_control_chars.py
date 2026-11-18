"""Regression: poll question/option text must reject control characters.

Poll text is stored verbatim and delivered to every viewer in REST responses
and gateway payloads. C0/DEL/C1 controls and Unicode line/paragraph separators
can forge a log line or split a downstream frame, so the creation validators
refuse them outright (the same rule presence status/activity/emoji already
follow). These tests pin that rule and make sure ordinary text still passes.
"""

import pytest

from src.core.polls.exceptions import (
    InvalidPollOptionError,
    InvalidPollQuestionError,
)
from src.core.polls.limits import validate_option_text, validate_question_text


CONTROL_VALUES = [
    "Yes\nNo",  # LF — log/frame forging
    "Yes\rNo",  # CR
    "Yes\x00No",  # NUL
    "Yes\tNo",  # TAB
    "Yes\x7fNo",  # DEL
    "Yes\x85No",  # C1 NEL — treated as a newline by many shippers
    "Yes\u2028No",  # LINE SEPARATOR
    "Yes\u2029No",  # PARAGRAPH SEPARATOR
]


@pytest.mark.polls
class TestPollTextRejectsControlCharacters:
    @pytest.mark.parametrize("value", CONTROL_VALUES)
    def test_question_rejects_control_characters(self, value):
        with pytest.raises(InvalidPollQuestionError):
            validate_question_text(value, {})

    @pytest.mark.parametrize("value", CONTROL_VALUES)
    def test_option_rejects_control_characters(self, value):
        with pytest.raises(InvalidPollOptionError):
            validate_option_text(value, {})

    def test_question_allows_ordinary_text(self):
        assert validate_question_text("  Lunch?  ", {}) == "Lunch?"

    def test_option_allows_ordinary_text(self):
        assert validate_option_text("  Yes  ", {}) == "Yes"

    def test_manager_create_rejects_control_chars_in_question(self, dm_with_message):
        user1, _user2, _dm, msg, polls, _messaging = dm_with_message
        with pytest.raises(InvalidPollQuestionError):
            polls.create_poll(
                user_id=user1.id,
                message_id=msg.id,
                question="Vote\nnow",
                options=["Yes", "No"],
            )

    def test_manager_create_rejects_control_chars_in_option(self, dm_with_message):
        user1, _user2, _dm, msg, polls, _messaging = dm_with_message
        with pytest.raises(InvalidPollOptionError):
            polls.create_poll(
                user_id=user1.id,
                message_id=msg.id,
                question="Vote now?",
                options=["Yes", "No\x00pe"],
            )
