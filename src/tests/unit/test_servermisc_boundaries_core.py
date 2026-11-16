"""Focused regression tests for the server/core boundary validators.

These call the shared helpers directly. The HTTP schemas cover the same rules
for ordinary requests, but these helpers are what protect direct callers
(fixtures, gateway, imports) that bypass FastAPI entirely.
"""

from __future__ import annotations

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.servers.models import ChannelType
from src.core.servers.permission_utils import (
    MAX_PERMISSION_ENTRIES,
    validate_permission_dict,
)
from src.core.servers.validation import (
    MAX_SERVER_DESCRIPTION_LENGTH,
    MAX_SERVER_MEDIA_URL_LENGTH,
    coerce_channel_type,
    validate_server_description,
    validate_server_media_url,
)
from src.core.servers.exceptions import InvalidPermissionKeyError


class TestServerMediaUrls:
    def test_same_origin_upload_paths_are_accepted(self):
        assert (
            validate_server_media_url("/api/v1/avatars/servers/42")
            == "/api/v1/avatars/servers/42"
        )

    def test_empty_values_pass_through(self):
        assert validate_server_media_url(None) is None
        assert validate_server_media_url("") is None

    @pytest.mark.parametrize(
        "bad",
        [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "data:image/svg+xml;base64,AAA",
            "//evil.example/icon.png",
            "https://user:pass@example.com/icon.png",
        ],
    )
    def test_non_http_or_credentialed_urls_are_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_server_media_url(bad, "icon")

    def test_control_characters_and_overly_long_urls_are_rejected(self):
        with pytest.raises(HardeningViolation):
            validate_server_media_url("/api/x\x00y")
        with pytest.raises(HardeningViolation):
            validate_server_media_url("a" * (MAX_SERVER_MEDIA_URL_LENGTH + 1))


class TestServerDescription:
    def test_plain_description_is_accepted(self):
        assert validate_server_description("A friendly server") == "A friendly server"

    def test_control_characters_are_rejected(self):
        with pytest.raises(HardeningViolation):
            validate_server_description("hello\x1b[31m")

    def test_overlong_description_is_rejected(self):
        with pytest.raises(HardeningViolation):
            validate_server_description("x" * (MAX_SERVER_DESCRIPTION_LENGTH + 1))


class TestChannelTypeCoercion:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("text", ChannelType.TEXT),
            ("VOICE", ChannelType.VOICE),
            (ChannelType.STAGE, ChannelType.STAGE),
        ],
    )
    def test_valid_values_normalise_to_enum(self, raw, expected):
        assert coerce_channel_type(raw) is expected

    @pytest.mark.parametrize("bad", ["nonsense", "", 7, {"type": "text"}])
    def test_bare_or_unknown_values_are_rejected(self, bad):
        with pytest.raises(ValueError):
            coerce_channel_type(bad)


class TestPermissionBounds:
    @pytest.mark.parametrize(
        "bad",
        [
            {"*": True},
            {"messages.*": True},
            {"administrator": "yes"},
            {"a" * 70: True},
            {"bad\nkey": True},
            {f"perm{i}": True for i in range(MAX_PERMISSION_ENTRIES + 5)},
        ],
    )
    def test_hostile_permission_dicts_are_rejected(self, bad):
        with pytest.raises(InvalidPermissionKeyError):
            validate_permission_dict(bad)

    def test_boolean_known_permission_is_accepted(self):
        validate_permission_dict({"channels.view": True})
