"""Hardening and split-compat tests for the embed validator package."""

from __future__ import annotations

from typing import Tuple
from unittest.mock import patch

import pytest

from src.core.embeds.exceptions import (
    EmbedSanitizationError,
    EmbedValidationError,
    InvalidColorError,
    InvalidUrlError,
)
from src.core.embeds.validator import (
    ValidationResult,
    AUTHOR_NAME_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    DANGEROUS_PATTERNS,
    FIELD_NAME_MAX_LENGTH,
    FIELD_VALUE_MAX_LENGTH,
    FOOTER_TEXT_MAX_LENGTH,
    MAX_EMBEDS_PER_MESSAGE,
    MAX_FIELDS,
    MAX_URL_LENGTH,
    TITLE_MAX_LENGTH,
    TOTAL_CHAR_LIMIT,
    URL_PATTERN,
    _coerce_dimension,
    _public_media_origin,
    _strip_url_dangerous_unicode,
    sanitize_content,
    sanitize_message_embeds,
    validate_color,
    validate_embed_data,
    validate_string_length,
    validate_timestamp,
    validate_url,
)
from src.core.embeds.validator.message import _validate_message_color


class _PermissiveURLValidator:
    """Test double for src.utils.security.URLValidator.

    Accepts every public hostname and resolves it to 1.2.3.4 so URL
    acceptance tests are deterministic and offline-safe. IP literals and
    localhost-like hostnames are rejected, mirroring the real validator.
    """

    def validate_url_for_request(self, url: str) -> Tuple[str, str]:
        from ipaddress import ip_address
        from urllib.parse import urlparse

        hostname = (urlparse(url).hostname or "").lower()
        if (
            not hostname
            or hostname in {"localhost", "127.0.0.1", "::1"}
            or hostname.endswith((".local", ".internal"))
        ):
            raise ValueError(f"Access to {hostname} is forbidden")
        bare = hostname.strip("[]")
        try:
            ip = ip_address(bare)
        except ValueError:
            return hostname, "1.2.3.4"
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"Hostname {hostname} resolves to forbidden IP {bare}")
        return hostname, "1.2.3.4"


@pytest.fixture(autouse=True)
def _patch_url_validator():
    with patch(
        "src.utils.security.URLValidator",
        return_value=_PermissiveURLValidator(),
    ):
        yield


class TestValidatorPackageApi:
    def test_all_exports_accessible(self):
        import src.core.embeds.validator as module

        assert isinstance(module.__all__, list)
        for name in module.__all__:
            assert hasattr(module, name), f"missing export {name}"

    def test_public_api_callables(self):
        import src.core.embeds.validator as module

        assert callable(module.validate_url)
        assert callable(module.validate_color)
        assert callable(module.validate_timestamp)
        assert callable(module.sanitize_content)
        assert callable(module.validate_string_length)
        assert callable(module.sanitize_message_embeds)
        assert callable(module.validate_embed_data)
        assert callable(module.calculate_embed_chars)

    def test_constants(self):
        assert TITLE_MAX_LENGTH == 256
        assert DESCRIPTION_MAX_LENGTH == 4096
        assert FIELD_NAME_MAX_LENGTH == 256
        assert FIELD_VALUE_MAX_LENGTH == 1024
        assert FOOTER_TEXT_MAX_LENGTH == 2048
        assert AUTHOR_NAME_MAX_LENGTH == 256
        assert TOTAL_CHAR_LIMIT == 6000
        assert MAX_FIELDS == 25
        assert MAX_EMBEDS_PER_MESSAGE == 10
        assert MAX_URL_LENGTH == 2048
        assert len(DANGEROUS_PATTERNS) > 0

    def test_url_pattern_shape(self):
        assert URL_PATTERN.match("https://example.com/x") is not None
        assert URL_PATTERN.match("http://example.com:8080/x") is not None
        assert URL_PATTERN.match("ftp://example.com") is None
        assert URL_PATTERN.match("http://localhost") is None

    def test_private_helpers_accessible(self):
        import src.core.embeds.validator as module

        assert callable(module._strip_url_dangerous_unicode)
        assert callable(module._public_media_origin)
        assert callable(module._coerce_dimension)

    def test_submodule_paths_resolve(self):
        import src.core.embeds.validator.constants as constants
        import src.core.embeds.validator.content as content
        import src.core.embeds.validator.message as message
        import src.core.embeds.validator.schema as schema
        import src.core.embeds.validator.url_validation as url_validation

        assert constants.MAX_EMBEDS_PER_MESSAGE == MAX_EMBEDS_PER_MESSAGE
        assert callable(content.sanitize_content)
        assert callable(message.sanitize_message_embeds)
        assert callable(schema.validate_embed_data)
        assert callable(url_validation.validate_url)

    def test_validation_result_instantiable(self):
        result = ValidationResult(valid=True, issues=[], total_chars=0)
        assert result.valid is True
        assert result.issues == []
        assert result.total_chars == 0
        assert result.sanitized_data is None


class TestUrlValidationAcceptance:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com",
            "http://example.com:8080/x",
            "https://example.com/path/to/img.png?w=100&h=100",
            "https://xn--e1afmkfd.xn--p1ai",
        ],
    )
    def test_legitimate_urls_accepted(self, url):
        assert validate_url(url, "url") == url

    def test_idn_url_normalised_to_punycode(self):
        assert validate_url("https://пример.рф") == "https://xn--e1afmkfd.xn--p1ai"

    def test_relative_media_path_preserved_without_public_origin(self):
        result = validate_url("/api/v1/media/uploads/avatar.png", "url")
        assert result == "/api/v1/media/uploads/avatar.png" or result.endswith(
            "/api/v1/media/uploads/avatar.png"
        )


class TestUrlValidationRejection:
    @pytest.mark.parametrize(
        "url",
        [
            "not_a_url",
            "ftp://example.com/x",
            "http://",
            "https://",
            "javascript:alert(1)",
            "JAVASCRIPT:alert(1)",
            "data:text/html,<h1>x</h1>",
            "vbscript:msgbox(1)",
            'https://e.com/a.png"onerror="alert(document.cookie)',
            "https://e.com/a.png\x01onerror=alert(1)",
            "https://e.com/a.png\x7f",
            "`<img src=x onerror=`",
            "/api/v1/media/../../etc/passwd",
            "/api/v1/media/../x.png",
            "/foo/bar.png",
            "https://example.com\\..\\x",
            "http://127.0.0.1/x.png",
            "http://10.0.0.5/x.png",
            "http://[::1]/x.png",
            "http://example.local/x.png",
            "http://example.internal/x.png",
        ],
    )
    def test_malicious_urls_rejected(self, url):
        with pytest.raises(InvalidUrlError):
            validate_url(url, "url")

    def test_oversized_url_rejected(self):
        long_url = "https://" + "a" * (MAX_URL_LENGTH + 10) + ".com"
        with pytest.raises(InvalidUrlError, match="exceeds maximum length"):
            validate_url(long_url, "url")

    def test_empty_url_passes_through(self):
        assert validate_url("") == ""


class TestContentSanitization:
    def test_safe_content_passes(self):
        assert sanitize_content("Hello world!") == "Hello world!"
        assert sanitize_content("done=complete") == "done=complete"
        assert sanitize_content("phone=555-1234") == "phone=555-1234"
        assert sanitize_content("onboarding=yes") == "onboarding=yes"
        assert sanitize_content("") == ""

    @pytest.mark.parametrize(
        "content",
        [
            "<script>alert(1)</script>",
            "<SCRIPT>alert(1)</SCRIPT>",
            "<iframe src='evil.com'></iframe>",
            "<object data='evil.swf'></object>",
            "<embed src='evil.swf'>",
            "<form></form>",
            "onclick=alert('xss')",
            "<div onclick=alert(1)>click</div>",
            "jav\u0009ascript:alert(1)",
            "&lt;script&gt;alert(1)&lt;/script&gt;",
            "%3Cscript%3Ealert(1)%3C/script%3E",
            "<img src=x onerror=alert(1)>",
            '<img src=x onerror="alert(1)">',
        ],
    )
    def test_dangerous_content_rejected(self, content):
        with pytest.raises(EmbedSanitizationError):
            sanitize_content(content, "title")


class TestMessageEmbedSanitization:
    def test_none_and_empty(self):
        assert sanitize_message_embeds(None) is None
        assert sanitize_message_embeds([]) == []

    def test_non_list_rejected(self):
        with pytest.raises(EmbedValidationError, match="must be a list"):
            sanitize_message_embeds({"title": "x"})

    def test_embed_limit_enforced(self):
        with pytest.raises(EmbedValidationError, match="more than 10"):
            sanitize_message_embeds([{"title": f"e{i}"} for i in range(11)])

    def test_field_limit_enforced(self):
        with pytest.raises(EmbedValidationError, match="more than 25"):
            sanitize_message_embeds(
                [
                    {
                        "title": "x",
                        "fields": [{"name": f"n{i}", "value": "v"} for i in range(26)],
                    }
                ]
            )

    def test_clean_embed_sanitized(self):
        result = sanitize_message_embeds(
            [
                {
                    "title": "Join us",
                    "color": 0x5865F2,
                    "fields": [{"name": "Members", "value": "123"}],
                    "thumbnail": {"url": "https://example.com/a.png"},
                }
            ]
        )
        assert result[0]["color"] == "#5865F2"
        assert result[0]["fields"][0]["name"] == "Members"
        assert result[0]["thumbnail"]["url"] == "https://example.com/a.png"

    def test_xss_in_field_value_rejected(self):
        with pytest.raises(EmbedSanitizationError):
            sanitize_message_embeds(
                [
                    {
                        "type": "server_invite",
                        "fields": [
                            {
                                "name": "Members",
                                "value": "<img src=x onerror=alert(1)>",
                            }
                        ],
                    }
                ]
            )

    def test_javascript_url_rejected(self):
        with pytest.raises(InvalidUrlError):
            sanitize_message_embeds(
                [{"type": "sticker", "url": "javascript:alert(1)", "title": "x"}]
            )

    def test_private_ip_url_rejected(self):
        with pytest.raises(InvalidUrlError):
            sanitize_message_embeds(
                [{"type": "sticker", "url": "http://127.0.0.1/x.png", "title": "x"}]
            )


class TestValidateEmbedData:
    def test_valid_embed(self):
        result = validate_embed_data({"title": "Hello", "description": "World"})
        assert result.valid is True
        assert result.issues == []
        assert result.total_chars == 10
        assert result.sanitized_data is not None

    def test_invalid_embed(self):
        result = validate_embed_data({"title": "a" * 300, "url": "invalid-url"})
        assert result.valid is False
        assert len(result.issues) > 0

    def test_total_char_limit_exceeded(self):
        result = validate_embed_data(
            {
                "title": "a" * 256,
                "description": "b" * 4096,
                "footer": {"text": "c" * 2000},
            }
        )
        assert not result.valid
        assert any(
            "6000" in issue or "character" in issue.lower() for issue in result.issues
        )

    def test_dimension_coercion_no_crash(self):
        result = validate_embed_data(
            {"image": {"url": "https://example.com/x.png", "width": "abc"}}
        )
        assert not result.valid
        assert any("width" in issue.lower() for issue in result.issues)


class TestScalarValidators:
    def test_validate_string_length(self):
        assert validate_string_length("hi", 10, "field") == "hi"
        with pytest.raises(EmbedValidationError):
            validate_string_length("too long", 5, "field")

    def test_validate_color(self):
        assert validate_color("#F00") == "#FF0000"
        assert validate_color("00FF00") == "#00FF00"
        assert validate_color("#ffffff") == "#FFFFFF"
        assert validate_color("") == ""
        with pytest.raises(InvalidColorError):
            validate_color("#GGGGGG")
        with pytest.raises(InvalidColorError):
            validate_color("not-a-color")

    def test_validate_message_color(self):
        assert _validate_message_color(0x5865F2, "embed.color") == "#5865F2"
        with pytest.raises(InvalidColorError):
            _validate_message_color(True, "embed.color")
        with pytest.raises(InvalidColorError):
            _validate_message_color(-1, "embed.color")
        with pytest.raises(InvalidColorError):
            _validate_message_color(0x1000000, "embed.color")

    def test_validate_timestamp(self):
        assert validate_timestamp("2026-01-01T12:00:00Z") == "2026-01-01T12:00:00Z"
        assert validate_timestamp("2026-01-01") == "2026-01-01"
        assert validate_timestamp("") == ""
        with pytest.raises(EmbedValidationError):
            validate_timestamp("not-a-timestamp")


class TestInternalHelpers:
    def test_strip_url_dangerous_unicode(self):
        url = "https://example.com"
        assert _strip_url_dangerous_unicode("a\u200bb\u0001c\u007fd") == "abcd"
        assert _strip_url_dangerous_unicode(url) == url

    def test_public_media_origin_none_by_default(self):
        assert _public_media_origin() is None

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, None),
            ("", None),
            (100, 100),
            (50.0, 50),
            (" 100 ", 100),
        ],
    )
    def test_coerce_dimension_valid(self, value, expected):
        issues: list[str] = []
        assert _coerce_dimension(value, "width", issues) == expected
        assert issues == []

    @pytest.mark.parametrize("value", [True, False, 50.5, -5, "abc", [1, 2]])
    def test_coerce_dimension_invalid(self, value):
        issues: list[str] = []
        assert _coerce_dimension(value, "width", issues) is None
        assert len(issues) == 1
