"""Presence/status payload field and length validation."""

import pytest

from src.api.websocket.boundaries import (
    MAX_ACTIVITIES,
    MAX_CUSTOM_STATUS_LEN,
    MAX_EMOJI_LEN,
    MAX_STATUS_LEN,
    validate_presence_payload,
)
from src.core.security.hardening import HardeningViolation


def test_valid_presence_payload_normalized():
    result = validate_presence_payload(
        {
            "status": "online",
            "custom_status": "working",
            "custom_emoji": ":code:",
            "activities": [{"type": 0, "name": "PlexiChat"}],
        }
    )
    assert result["status"] == "online"
    assert result["custom_status"] == "working"
    assert result["custom_emoji"] == ":code:"
    assert result["activities"][0]["name"] == "PlexiChat"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "x" * (MAX_STATUS_LEN + 1)},
        {"status": "on\x00line"},
        {"custom_status": "x" * (MAX_CUSTOM_STATUS_LEN + 1)},
        {"custom_emoji": "x" * (MAX_EMOJI_LEN + 1)},
        {"activities": [{"type": 0}] * (MAX_ACTIVITIES + 1)},
        {"activities": "not-a-list"},
        {"activities": ["not-a-dict"]},
        {"activities": [{"type": 0, "name": "x" * 500}]},
        {"status": 12345},
    ],
)
def test_invalid_presence_payloads_rejected(payload):
    with pytest.raises(HardeningViolation):
        validate_presence_payload(payload)
