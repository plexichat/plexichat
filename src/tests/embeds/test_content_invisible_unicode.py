"""Regression: invisible Unicode must not split dangerous embed tokens.

NFKC normalisation leaves zero-width and other ``Cf`` format characters
intact, and ``\\s`` does not match them. A payload such as ``java\\u200bscript:``
therefore used to bypass every matcher form in ``sanitize_content`` even
though a human sees an ordinary ``javascript:`` URL.
"""

import pytest

from src.core.embeds.exceptions import EmbedSanitizationError
from src.core.embeds.validator.content import _is_invisible, sanitize_content


# Each payload is the spiced-up equivalent of a payload the blocklist already
# rejects. The inserted characters are invisible in rendered text.
INVISIBLE_XSS_PAYLOADS = [
    "java\u200bscript:alert(1)",  # zero-width space
    "java\u200cscript:alert(1)",  # zero-width non-joiner
    "java\u200dscript:alert(1)",  # zero-width joiner
    "vbscript\u2060:alert(1)",  # word joiner
    "data\u00ad:text/html,<script>alert(1)</script>",  # soft hyphen
    "<scr\u200bipt>alert(1)</scr\u200bipt>",
    "<img src=x onerror\u200b=alert(1)>",
]


@pytest.mark.parametrize("payload", INVISIBLE_XSS_PAYLOADS)
def test_invisible_unicode_cannot_bypass_dangerous_patterns(payload):
    with pytest.raises(EmbedSanitizationError):
        sanitize_content(payload, "description")


@pytest.mark.parametrize(
    "text",
    [
        "Hello world",
        "Caf\u00e9 \u2014 r\u00e9sum\u00e9",  # accents survive NFKC matching
        "emoji \u200d sequence",  # ZWJ in legitimate text stays untouched
        "phone=123 onboarding=true done=",  # benign '=' prose stays allowed
    ],
)
def test_legitimate_text_is_not_rejected(text):
    assert sanitize_content(text, "description") == text


@pytest.mark.parametrize(
    "ch",
    [
        "\u200b",
        "\u200c",
        "\u200d",
        "\u2060",
        "\ufeff",
        "\u00ad",
        "\x00",
        "\x7f",
        "\x85",
    ],
)
def test_invisible_predicate_covers_format_and_control_chars(ch):
    assert _is_invisible(ch)


def test_visible_ascii_is_not_invisible():
    assert not _is_invisible("a")
    assert not _is_invisible("1")
