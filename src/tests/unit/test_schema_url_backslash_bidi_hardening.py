"""Regression: URL backslash confusion and full bidi-control coverage.

Two gaps at the schema boundary:

* Browsers and HTTP clients normalise a raw ``\\`` to ``/`` in special schemes
  (http/https), but ``urlparse`` does not. A URL validated against one
  authority can therefore be rendered or fetched with another. ``validate_http_url``
  now refuses a raw backslash anywhere in the URL.

* ``reject_unsafe_unicode`` covered most of Unicode's ``Bidi_Control`` set but
  missed U+070F (Syriac abbreviation mark, a strong right-to-left control) and
  the legacy directional formats U+206A-U+206F, which let a stored string render
  as something else. They are now rejected for every policy-annotated text
  field and for URL fields (which share the rule).
"""

import pytest
from pydantic import BaseModel, Field, ValidationError

from src.api.schemas.policy_types import UIText, reject_unsafe_unicode
from src.api.schemas.url_types import HttpUrl, validate_http_url
from src.core.security.errors import OutboundUrlRejected, TextRejected


# ---------------------------------------------------------------------------
# 1. Raw backslash in URLs
# ---------------------------------------------------------------------------

BACKSLASH_URLS = [
    "https://example.tld\\",  # trailing separator: browser -> "https://example.tld/"
    "https://example.tld\\evil.tld",  # authority/path confusion
    "https://exa\\mple.tld/x",  # host confusion
    "https://allow.example.tld\\@evil.tld/x",  # credential + backslash split
    "http://example.tld/a\\b",  # raw backslash in the path
]


@pytest.mark.parametrize("url", BACKSLASH_URLS)
def test_url_backslash_rejected(url):
    with pytest.raises(OutboundUrlRejected, match="backslash"):
        validate_http_url(url, "url")


class _UrlCarrier(BaseModel):
    url: HttpUrl = Field(..., min_length=1)


def test_url_annotation_also_rejects_backslash():
    with pytest.raises(ValidationError):
        _UrlCarrier(url="https://example.tld\\evil.tld")


def test_percent_encoded_backslash_and_normal_urls_still_accepted():
    # A literal backslash must be encoded by the caller; that stays valid.
    assert (
        validate_http_url("https://example.tld/a%5Cb", "url")
        == "https://example.tld/a%5Cb"
    )
    assert validate_http_url("https://example.tld/a/b?x=1#f", "url").endswith(
        "a/b?x=1#f"
    )


# ---------------------------------------------------------------------------
# 2. Missing Unicode bidi controls
# ---------------------------------------------------------------------------

BIDI_CONTROLS = [
    "\u070f",  # SYRIAC ABBREVIATION MARK (strong RTL, Bidi_Control)
    "\u206a",  # INHIBIT SYMMETRIC SWAPPING
    "\u206b",  # ACTIVATE SYMMETRIC SWAPPING
    "\u206c",  # INHIBIT ARABIC FORM SHAPING
    "\u206d",  # ACTIVATE ARABIC FORM SHAPING
    "\u206e",  # NATIONAL DIGIT SHAPES
    "\u206f",  # NOMINAL DIGIT SHAPES
]


@pytest.mark.parametrize("control", BIDI_CONTROLS)
def test_missing_bidi_controls_rejected(control):
    with pytest.raises(TextRejected):
        reject_unsafe_unicode("ok" + control + "x", "value")


class _TextCarrier(BaseModel):
    value: UIText = Field(..., min_length=1)


@pytest.mark.parametrize("control", BIDI_CONTROLS)
def test_bidi_controls_rejected_through_policy_annotation(control):
    with pytest.raises(ValidationError):
        _TextCarrier(value="ok" + control + "x")


@pytest.mark.parametrize("control", BIDI_CONTROLS)
def test_bidi_controls_rejected_in_urls(control):
    # URL fields reuse the same shared unicode rule.
    with pytest.raises(TextRejected):
        validate_http_url("https://example.tld/" + control + "x", "url")


def test_ordinary_unicode_and_emoji_zwj_still_accepted():
    assert reject_unsafe_unicode("café 👨‍👩‍👧", "value") == "café 👨‍👩‍👧"
