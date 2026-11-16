"""Shared validators for presence status and custom-emoji fields.

Status and emoji arrive from several entry points (the REST route, the
WebSocket handler and background jobs). Validating in one place means every
caller gets the same bounds: a status must be one of the known states, and an
emoji must be a short, control-character-free token that cannot break a log
line or a downstream payload.
"""

from __future__ import annotations

from typing import Optional, Union

from src.core.security.hardening import HardeningViolation, reject_control_characters

from .models import UserStatus

VALID_STATUSES = frozenset(status.value for status in UserStatus)
MAX_EMOJI_CHARS = 64
MAX_STATUS_TEXT_CHARS = 128


def validate_status(value: Union[UserStatus, str]) -> UserStatus:
    """Return the matching :class:`UserStatus`, refusing any other value."""
    if isinstance(value, UserStatus):
        return value
    text = reject_control_characters(str(value or "").strip().lower(), "status")
    if text not in VALID_STATUSES:
        raise HardeningViolation("invalid presence status")
    return UserStatus(text)


def validate_emoji(value: Optional[str]) -> Optional[str]:
    """Return a bounded, control-character-free custom emoji (or ``None``)."""
    if value is None:
        return None
    text = reject_control_characters(str(value).strip(), "custom_emoji")
    if not text:
        return None
    if len(text) > MAX_EMOJI_CHARS:
        raise HardeningViolation("custom_emoji exceeds the length limit")
    if any(character.isspace() for character in text):
        raise HardeningViolation("custom_emoji must not contain whitespace")
    return text


def validate_status_text(
    value: Optional[str], maximum: int = MAX_STATUS_TEXT_CHARS
) -> str:
    """Return a bounded, control-character-free custom status text.

    An empty string is preserved (an emoji-only status stores empty text); a
    too-long value or any C0/DEL control character is refused outright rather
    than silently truncated, so a hostile status cannot forge a log/WS frame.
    """
    if value is None:
        return ""
    text = reject_control_characters(str(value).strip(), "custom_status")
    if len(text) > maximum:
        raise HardeningViolation("custom_status exceeds the length limit")
    return text


__all__ = [
    "VALID_STATUSES",
    "MAX_EMOJI_CHARS",
    "MAX_STATUS_TEXT_CHARS",
    "validate_status",
    "validate_emoji",
    "validate_status_text",
]
