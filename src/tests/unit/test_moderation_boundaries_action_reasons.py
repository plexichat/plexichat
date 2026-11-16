"""Action enum, reason and duration boundaries."""

import pytest

from src.core.automod.exceptions import RuleValidationError
from src.core.automod.limits import MAX_REASON_LENGTH, bounded_reason, clip_text
from src.core.automod.manager.rules import _parse_action
from src.core.automod.models import ActionType


def test_bounded_reason():
    assert bounded_reason("spam", "reason") == "spam"
    with pytest.raises(ValueError):
        bounded_reason("a" * (MAX_REASON_LENGTH + 1), "reason")
    with pytest.raises(ValueError):
        bounded_reason("a\nb", "reason")


def test_clip_text_removes_controls_and_truncates():
    text = "a" * 1000 + "\n" + "b"
    result = clip_text(text, MAX_REASON_LENGTH)
    assert len(result) == MAX_REASON_LENGTH
    assert "\n" not in result


def test_parse_action_validates_reason_and_duration():
    action = _parse_action(
        {
            "action_type": "timeout_user",
            "duration_seconds": 3600,
            "reason": "ok",
        }
    )
    assert action.action_type is ActionType.TIMEOUT_USER
    assert action.duration_seconds == 3600
    assert action.reason == "ok"
    with pytest.raises(RuleValidationError):
        _parse_action({"action_type": "timeout_user", "reason": "x" * 1000})
    with pytest.raises(RuleValidationError):
        _parse_action({"action_type": "ban_user", "reason": "bad\nreason"})
    with pytest.raises(RuleValidationError):
        _parse_action({"action_type": "ban_user", "duration_seconds": 10**12})
