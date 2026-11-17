"""Deepening of the channel request/response text boundaries.

The route-level ``validate_channel_fields`` used to reject only C0 control
characters, while the request schema's ``UIText`` also rejects HTML
metacharacters. That gap let any caller that bypassed Pydantic (a widened
schema, an internal caller) persist a stored-XSS payload past the route guard.
It also never checked for unexpected keys, so a schema with ``extra='allow'``
could turn ``PATCH /channels/{id}`` into a mass-assignment primitive.

These tests pin the strengthened guards and the response-side defence in depth.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes.channels.helpers import (
    MAX_CHANNEL_NAME_LENGTH,
    MAX_CHANNEL_TOPIC_LENGTH,
    _channel_to_response,
    validate_channel_fields,
)


class TestRouteTextPolicyMatchesSchema:
    @pytest.mark.parametrize(
        "payload",
        [
            {"name": "<script>alert(1)</script>"},
            {"name": 'img src=x onerror="alert(1)"'},
            {"topic": "<img src=x onerror=alert(1)>"},
            {"topic": "a&b=c"},
            {"name": "bad\x00name"},
        ],
    )
    def test_unsafe_text_is_rejected_at_the_route_boundary(self, payload):
        with pytest.raises(HTTPException) as exc:
            validate_channel_fields(payload)
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": "general"},
            {"topic": "a normal topic"},
            {"name": "a" * MAX_CHANNEL_NAME_LENGTH},
            {"topic": "b" * MAX_CHANNEL_TOPIC_LENGTH},
            {"nsfw": True, "slowmode_seconds": 10, "read_receipts_enabled": False},
            {"position": 3},
            {"channel_type": "text"},
            {"category_id": 12345},
        ],
    )
    def test_legitimate_updates_still_pass(self, payload):
        validate_channel_fields(payload)


class TestMassAssignmentGuard:
    @pytest.mark.parametrize(
        "payload",
        [
            {"server_id": 999},
            {"owner_id": 999},
            {"id": 999},
            {"inviter_id": 999},
            {"recipient_id": 999},
            {"server_id": 999, "name": "looks harmless"},
        ],
    )
    def test_unexpected_update_keys_are_rejected(self, payload):
        with pytest.raises(HTTPException) as exc:
            validate_channel_fields(payload)
        assert exc.value.status_code == 400

    def test_non_mapping_payload_is_rejected(self):
        with pytest.raises(HTTPException) as exc:
            validate_channel_fields(["name", "general"])
        assert exc.value.status_code == 400


class TestChannelTypeStrictness:
    @pytest.mark.parametrize(
        "value",
        [
            {"value": "text"},
            ["text"],
            42,
            "unknown-type",
            "",
        ],
    )
    def test_non_string_or_unknown_types_are_rejected(self, value):
        with pytest.raises(HTTPException) as exc:
            validate_channel_fields({"channel_type": value})
        assert exc.value.status_code == 400

    def test_enum_like_value_is_still_accepted(self):
        validate_channel_fields({"channel_type": SimpleNamespace(value="voice")})


class TestResponseSideSanitization:
    @staticmethod
    def _channel(name, topic):
        return SimpleNamespace(
            id=123456789,
            server_id=987654321,
            channel_type="text",
            name=name,
            topic=topic,
            position=0,
            category_id=None,
            nsfw=False,
            slowmode_seconds=0,
            read_receipts_enabled=True,
            created_at=1,
        )

    def test_control_characters_are_stripped_from_echoed_text(self):
        response = _channel_to_response(
            self._channel("general\x00\x1f", "topic\r\nforged")
        )
        assert "general" in response.name
        assert "\x00" not in response.name and "\x1f" not in response.name
        assert "\r" not in response.topic and "\n" not in response.topic

    def test_echoed_text_is_length_bounded(self):
        response = _channel_to_response(
            self._channel("x" * 5000, "y" * 5000)
        )
        assert len(response.name) <= MAX_CHANNEL_NAME_LENGTH
        assert len(response.topic) <= MAX_CHANNEL_TOPIC_LENGTH

    def test_none_topic_stays_none(self):
        response = _channel_to_response(self._channel("general", None))
        assert response.topic is None
