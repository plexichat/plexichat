"""Regression: soundboard PATCH may not store unsafe ``emoji`` text.

The upload route runs ``emoji`` through the shared UI-text policy because the
value is rendered to every server member in the soundboard picker (stored-XSS
defence). ``SoundUpdateRequest`` used to declare it as a bare ``str`` with only
a length cap, so the PATCH endpoint accepted HTML metacharacters and control
characters that the create path rejects -- a bypass of that sanitisation.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.soundboard import SoundUpdateRequest
from src.tests.unit.test_schema_policy_coverage import _policy_name

pytestmark = [pytest.mark.security, pytest.mark.unit]

# Payloads that fit the previous 32-character cap but carry HTML/attribute
# breakout characters. Each one was accepted by PATCH before the fix.
XSS_EMOJI = (
    "<svg/onload=alert(1)>",
    "<img src=x onerror=1>",
    "<b>loud</b>",
    "a<b",
    "x=y",
    "a'b",
    'a"b',
    "a&b",
)

# Invisible/control text: corrupts logs and lets one string render as another.
CONTROL_EMOJI = (
    "ok\x00bad",
    "ok\x1b[31m",
    "ok\u202ebad",
    "ok\u200fbad",
)

ORDINARY_EMOJI = ("loudspeaker", "🔊", "🎉", "👍🏽")


def test_update_emoji_field_declares_ui_text_policy():
    """The field must be ratcheted to a named policy, not a free-form str."""
    assert _policy_name(SoundUpdateRequest.model_fields["emoji"]) == "ui_text"


@pytest.mark.parametrize("payload", XSS_EMOJI)
def test_update_rejects_html_metacharacter_emoji(payload):
    with pytest.raises(ValidationError):
        SoundUpdateRequest(emoji=payload)


@pytest.mark.parametrize("payload", CONTROL_EMOJI)
def test_update_rejects_control_and_bidi_emoji(payload):
    with pytest.raises(ValidationError):
        SoundUpdateRequest(emoji=payload)


@pytest.mark.parametrize("payload", ORDINARY_EMOJI)
def test_update_accepts_ordinary_emoji(payload):
    assert SoundUpdateRequest(emoji=payload).emoji == payload


def test_update_still_bounds_emoji_length():
    with pytest.raises(ValidationError):
        SoundUpdateRequest(emoji="a" * 33)
