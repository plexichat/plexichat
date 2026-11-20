"""Regression: browser-normalised backslash protocol-relative URLs are unsafe.

The WHATWG URL parser maps ``\\`` to ``/`` for special schemes (http/https/
file), so ``\\\\host/x`` is a network-path reference exactly like ``//host/x``.
``is_safe_media_url`` used to reject only ``//host`` and ``/\\host``, letting a
double-backslash value pass and resolve to an attacker-controlled origin when
rendered into an ``<audio src>`` / ``<img src>`` attribute.
"""

import pytest

from src.core.avatars.manager.url_guard import (
    is_safe_media_url,
    normalized_url,
)


@pytest.mark.parametrize(
    "raw",
    [
        r"\\evil.example/x.mp3",
        r"\\/evil.example/x.mp3",
        r"/\evil.example/x.mp3",
        r"\\127.0.0.1:8080/x.mp3",
        r"\\javascript:alert(1)",
        # HTML-entity encoded backslash array -> same after normalisation.
        r"&#92;&#92;evil.example/x.mp3",
    ],
)
def test_backslash_protocol_relative_urls_rejected(raw):
    assert is_safe_media_url(raw) is False


@pytest.mark.parametrize(
    "raw",
    [
        # A single leading backslash is a same-origin (relative) path, just
        # like "/x": browsers map it to "/x". It is not protocol-relative.
        r"\media\x.mp3",
        "/media/x.mp3",
        "https://cdn.example.com/x.mp3",
        "HTTPS://cdn.example.com/x.mp3",
        "cdn.example.com/x.mp3",
    ],
)
def test_single_backslash_relative_and_absolute_still_allowed(raw):
    assert is_safe_media_url(raw) is True


def test_normalized_url_is_unchanged_by_the_verdict():
    # The verdict uses a normalised copy; the source value is never rewritten.
    raw = r"\\evil.example/x.mp3"
    assert normalized_url(raw) == raw
