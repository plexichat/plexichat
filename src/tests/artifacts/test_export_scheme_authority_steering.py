"""Regression tests for scheme-based authority steering in artifact exports.

The standalone HTML artifact export embeds sanitized, user-authored rich text.
Its URL allowlist decides whether a value is a benign relative link by looking
for the literal substring ``"://"``. That is not how a browser parses URLs:
for special schemes a single slash or a backslash after ``scheme:`` is
normalised, so ``http:\\attacker.example`` and ``https:/\\attacker.example``
are resolved as absolute URLs to an attacker-chosen authority.

The existing authority test only covers network-path references (``//host``
and its backslash spellings). This covers the separate scheme-plus-backslash
spellings, including the obfuscated control-character variants the sanitizer
strips before checking.
"""

from __future__ import annotations

import pytest

from src.api.routes.artifacts_export import _safe_url, sanitize_rich_html


@pytest.mark.parametrize(
    "url",
    [
        "http:\\attacker.example/phish",
        "http:/\\attacker.example/phish",
        "http:\\\\attacker.example\\phish",
        "https:\\attacker.example/phish",
        "HTTPS:/\\attacker.example/phish",
        # Control characters are stripped before the scheme check, so these
        # must be refused just like their plain spelling.
        "http:\x00attacker.example/phish",
        "http:\t\\attacker.example/phish",
    ],
)
def test_scheme_plus_backslash_is_not_a_benign_relative_url(url):
    assert _safe_url(url) is None


@pytest.mark.parametrize(
    "attribute",
    [
        '<a href="http:\\attacker.example/phish">click</a>',
        '<img src="https:/\\attacker.example/track.png">',
    ],
)
def test_sanitizer_drops_scheme_steering_urls(attribute):
    clean = sanitize_rich_html(attribute)
    lowered = clean.lower()
    assert "attacker.example" not in lowered
    assert "href=" not in lowered and "src=" not in lowered


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/a?b=c",
        "http://example.test/path",
        "mailto:user@example.test",
        "/local/path.html",
        "relative/path.html",
        "./sibling.html",
    ],
)
def test_valid_urls_still_pass(url):
    assert _safe_url(url) == url
