"""
Tests for server-side message embed sanitization.

The message-send API accepts raw embed dicts that are stored verbatim and
later rendered by the client. Without sanitization a malicious embed becomes
stored XSS. These tests cover the ``sanitize_message_embeds`` validator and
its enforcement at the messaging persistence boundary.
"""

import pytest

from src.core.embeds.exceptions import (
    EmbedSanitizationError,
    EmbedValidationError,
    InvalidColorError,
    InvalidUrlError,
)
from src.core.embeds.validator import sanitize_message_embeds


class TestSanitizeMessageEmbeds:
    """Direct unit tests for the sanitizer."""

    def test_none_passes_through(self):
        assert sanitize_message_embeds(None) is None

    def test_empty_list_passes_through(self):
        assert sanitize_message_embeds([]) == []

    def test_non_list_rejected(self):
        with pytest.raises(EmbedValidationError, match="must be a list"):
            sanitize_message_embeds({"title": "not a list"})

    def test_server_invite_embed_sanitized(self):
        embeds = [
            {
                "type": "server_invite",
                "title": "Join us",
                "description": "Click to join",
                "color": 0x5865F2,
                "fields": [{"name": "Members", "value": "123"}],
                "invite_code": "abc",
                "server_name": "My Server",
                "thumbnail": {"url": "https://example.com/a.png"},
            }
        ]
        result = sanitize_message_embeds(embeds)
        assert result[0]["color"] == "#5865F2"
        assert result[0]["fields"][0]["name"] == "Members"
        assert result[0]["fields"][0]["value"] == "123"
        assert result[0]["thumbnail"]["url"] == "https://example.com/a.png"

    def test_xss_in_field_value_rejected(self):
        embeds = [
            {
                "type": "server_invite",
                "title": "Join",
                "fields": [
                    {"name": "Members", "value": "<img src=x onerror=alert(1)>"}
                ],
            }
        ]
        with pytest.raises(EmbedSanitizationError):
            sanitize_message_embeds(embeds)

    def test_xss_in_description_rejected(self):
        with pytest.raises(EmbedSanitizationError):
            sanitize_message_embeds(
                [{"type": "generic", "description": "hi <script>alert(1)</script>"}]
            )

    def test_javascript_url_rejected(self):
        embeds = [{"type": "sticker", "url": "javascript:alert(1)", "title": "x"}]
        with pytest.raises((InvalidUrlError, EmbedValidationError)):
            sanitize_message_embeds(embeds)

    def test_data_url_rejected(self):
        embeds = [
            {
                "type": "sticker",
                "url": "data:text/html,<script>alert(1)</script>",
                "title": "x",
            }
        ]
        with pytest.raises((InvalidUrlError, EmbedValidationError)):
            sanitize_message_embeds(embeds)

    def test_private_ip_url_rejected(self):
        embeds = [{"type": "sticker", "url": "http://127.0.0.1/x.png", "title": "x"}]
        with pytest.raises((InvalidUrlError, EmbedValidationError)):
            sanitize_message_embeds(embeds)

    def test_embed_limit_enforced(self):
        embeds = [{"title": f"e{i}"} for i in range(11)]
        with pytest.raises(EmbedValidationError, match="more than 10"):
            sanitize_message_embeds(embeds)

    def test_field_limit_enforced(self):
        embeds = [
            {
                "title": "x",
                "fields": [{"name": f"n{i}", "value": "v"} for i in range(26)],
            }
        ]
        with pytest.raises(EmbedValidationError, match="more than 25"):
            sanitize_message_embeds(embeds)

    def test_color_int_out_of_range_rejected(self):
        with pytest.raises(InvalidColorError):
            sanitize_message_embeds([{"title": "x", "color": 999999999}])

    def test_color_int_negative_rejected(self):
        with pytest.raises(InvalidColorError):
            sanitize_message_embeds([{"title": "x", "color": -1}])

    def test_invalid_color_string_rejected(self):
        with pytest.raises(InvalidColorError):
            sanitize_message_embeds([{"title": "x", "color": "<script>"}])

    def test_nested_image_url_sanitized(self):
        embeds = [
            {
                "title": "x",
                "image": {
                    "url": "https://example.com/x.webp",
                    "width": 100,
                    "height": 50,
                },
            }
        ]
        result = sanitize_message_embeds(embeds)
        assert result[0]["image"]["url"] == "https://example.com/x.webp"
        assert result[0]["image"]["width"] == 100

    def test_timestamp_kept(self):
        embeds = [{"title": "x", "timestamp": "2026-01-01T12:00:00Z"}]
        result = sanitize_message_embeds(embeds)
        assert result[0]["timestamp"] == "2026-01-01T12:00:00Z"


class TestSendMessageSanitization:
    """Enforcement at the messaging persistence boundary."""

    def test_send_message_sanitizes_embed(self, fresh_users_with_dm):
        user1, user2, dm, _msg, _embeds, messaging = fresh_users_with_dm

        sent = messaging.send_message(
            user1.id,
            dm.id,
            "hello",
            embeds=[
                {
                    "type": "server_invite",
                    "title": "Join",
                    "color": 0x5865F2,
                    "fields": [{"name": "Members", "value": "42"}],
                }
            ],
        )

        stored = sent.metadata.get("embeds")
        assert stored is not None
        assert stored[0]["color"] == "#5865F2"

    def test_send_message_rejects_xss_embed(self, fresh_users_with_dm):
        user1, user2, dm, _msg, _embeds, messaging = fresh_users_with_dm

        with pytest.raises(EmbedSanitizationError):
            messaging.send_message(
                user1.id,
                dm.id,
                "hello",
                embeds=[
                    {
                        "type": "server_invite",
                        "title": "x",
                        "fields": [
                            {"name": "Members", "value": "<img src=x onerror=alert(1)>"}
                        ],
                    }
                ],
            )

    def test_send_message_rejects_javascript_sticker_url(self, fresh_users_with_dm):
        user1, user2, dm, _msg, _embeds, messaging = fresh_users_with_dm

        with pytest.raises((InvalidUrlError, EmbedValidationError)):
            messaging.send_message(
                user1.id,
                dm.id,
                "hello",
                embeds=[
                    {"type": "sticker", "url": "javascript:alert(1)", "title": "x"}
                ],
            )
