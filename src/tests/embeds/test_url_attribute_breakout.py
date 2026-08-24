"""
Regression tests for the stored-XSS attribute-breakout fix (H1).

An embed URL such as ``https://e.com/a.png"onerror="alert(1)`` contains
no whitespace, so it previously passed every server gate (scheme https,
public host, URL shape). The embedded double quote then terminated the
client's double-quoted ``src="..."`` attribute and the remainder was
parsed as markup — stored XSS firing for every viewer of the message.
``validate_url`` must reject attribute-breakout characters and control
characters outright.
"""

import pytest

from src.core.embeds.exceptions import (
    EmbedSanitizationError,
    InvalidUrlError,
)
from src.core.embeds.validator import sanitize_message_embeds, validate_url


class TestUrlAttributeBreakout:
    """validate_url must reject HTML-attribute breakout payloads."""

    @pytest.mark.parametrize(
        "payload",
        [
            # The exact exploit chain from the audit findings.
            'https://e.com/a.png"onerror="alert(document.cookie)',
            '`"><img src=x onerror=`',
            '"><img src=x onerror=alert(1)>',
            "https://e.com/a.png'onmouseover='alert(1)",
            "https://e.com/a.png`onerror=`alert(1)",
            "https://e.com/<script>alert(1)</script>/x.png",
            "https://e.com/a.png\x01onerror=alert(1)",
            "https://e.com/a.png\x7f",
        ],
    )
    def test_malicious_urls_rejected(self, payload):
        with pytest.raises(InvalidUrlError):
            validate_url(payload, "url")

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/a.png",
            "https://example.com/path/to/img.png?w=100&h=100",
            "http://example.com:8080/x",
        ],
    )
    def test_legitimate_urls_accepted(self, url):
        assert validate_url(url, "url") == url

    def test_idn_url_with_path_accepted(self):
        # IDN hosts are punycode-normalised (existing behaviour); the
        # non-ASCII path must survive untouched and no breakout check
        # may reject it.
        result = validate_url("https://пример.рф/картинка.png", "url")
        assert result == "https://xn--e1afmkfd.xn--p1ai/картинка.png"


class TestMessageEmbedsUrlBreakout:
    """sanitize_message_embeds must reject breakout URLs in URL keys."""

    def test_sticker_url_breakout_rejected(self):
        embeds = [
            {"type": "sticker", "title": "s", "url": '"><img src=x onerror=alert(1)>'}
        ]
        with pytest.raises((InvalidUrlError, EmbedSanitizationError)):
            sanitize_message_embeds(embeds)

    def test_thumbnail_url_quote_payload_rejected(self):
        embeds = [
            {
                "type": "server_invite",
                "server_name": "Evil",
                "thumbnail": {
                    "url": 'https://e.com/a.png"onerror="alert(document.cookie)'
                },
            }
        ]
        with pytest.raises(InvalidUrlError):
            sanitize_message_embeds(embeds)

    def test_url_key_dangerous_pattern_rejected(self):
        # Even a shape-valid https URL carrying an event-handler payload
        # in its query must be rejected for URL keys via the
        # DANGEROUS_PATTERNS blocklist applied to URL-typed keys.
        embeds = [{"type": "sticker", "url": "https://e.com/?onclick=alert(1)"}]
        with pytest.raises(EmbedSanitizationError):
            sanitize_message_embeds(embeds)

    def test_nested_url_key_rejected(self):
        embeds = [{"image": {"url": 'https://e.com/i.png"onerror="x'}}]
        with pytest.raises(InvalidUrlError):
            sanitize_message_embeds(embeds)
