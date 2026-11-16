"""Regression tests for presence status/text/emoji boundaries."""

import pytest

from src.core.presence.models import UserStatus
from src.core.presence.validation import (
    MAX_EMOJI_CHARS,
    MAX_STATUS_TEXT_CHARS,
    validate_emoji,
    validate_status,
    validate_status_text,
)
from src.core.security.hardening import HardeningViolation


class TestStatusValidation:
    def test_known_statuses(self):
        assert validate_status("online") is UserStatus.ONLINE
        assert validate_status(" DND ") is UserStatus.DND
        assert validate_status(UserStatus.IDLE) is UserStatus.IDLE

    @pytest.mark.parametrize("bad", ["bogus", "", "online\nX", None, 7])
    def test_unknown_or_unsafe_status_rejected(self, bad):
        with pytest.raises((HardeningViolation, TypeError)):
            validate_status(bad)


class TestEmojiValidation:
    def test_valid_emoji(self):
        assert validate_emoji(None) is None
        assert validate_emoji("  👍 ") == "👍"
        assert validate_emoji(":tada:") == ":tada:"

    @pytest.mark.parametrize(
        "bad", ["a\x00b", "a\r\nb", "two words", "x" * (MAX_EMOJI_CHARS + 1)]
    )
    def test_bad_emoji_rejected(self, bad):
        with pytest.raises(HardeningViolation):
            validate_emoji(bad)


class TestStatusTextValidation:
    def test_valid_text(self):
        assert validate_status_text(None) == ""
        assert validate_status_text("  working  ") == "working"

    @pytest.mark.parametrize(
        "bad", ["a\x00b", "a\r\nb", "x" * (MAX_STATUS_TEXT_CHARS + 1)]
    )
    def test_bad_text_rejected(self, bad):
        with pytest.raises(HardeningViolation):
            validate_status_text(bad)


class TestAllWritePathsUseSharedValidators:
    def test_route_and_managers_reference_validators(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        expectations = {
            "src/api/routes/presence.py": [
                "validate_status",
                "validate_status_text",
                "validate_emoji",
            ],
            "src/core/presence/manager/status.py": ["validate_status"],
            "src/core/presence/manager/custom_status.py": [
                "validate_emoji",
                "validate_status_text",
            ],
        }
        for relative, markers in expectations.items():
            source = (root / relative).read_text(encoding="utf-8")
            for marker in markers:
                assert marker in source, f"{relative} stopped using {marker}"
