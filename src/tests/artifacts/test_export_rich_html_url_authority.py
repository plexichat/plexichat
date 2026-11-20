"""Regression tests for authority-steering URLs in exported rich text.

The standalone HTML artifact export embeds sanitized, user-authored
``content_html`` verbatim. Its URL allowlist must not treat a network-path
reference (``//host``) as an ordinary relative link: in a document opened from
disk that resolves to a UNC share (``file://host/share``) and can leak the
viewer's credentials, and over HTTP it navigates to a different origin.
Browsers normalise backslashes to slashes in special schemes, so every
``//``-equivalent spelling is refused too.
"""

from __future__ import annotations

import pytest

from src.api.routes.artifacts_export import _safe_url, sanitize_rich_html


@pytest.mark.parametrize(
    "url",
    [
        "//attacker.example/share",
        "\\\\attacker.example\\share",
        "/\\attacker.example/share",
        "\\/attacker.example/share",
        # Control characters are stripped before the check, so obfuscated
        # network-path references must still be refused.
        "/\t/attacker.example/share",
        "\\\x00\\attacker.example/share",
        "//127.0.0.1/internal",
    ],
)
def test_network_path_references_are_not_benign_relative_urls(url):
    assert _safe_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "/local/path.html",
        "relative/path.html",
        "./sibling.html",
        "../parent.html",
        "https://example.test/a?b=c",
        "mailto:user@example.test",
    ],
)
def test_genuine_relative_and_absolute_urls_still_pass(url):
    assert _safe_url(url) == url


@pytest.mark.parametrize(
    "attribute",
    [
        '<a href="//attacker.example/phish">click</a>',
        '<img src="\\\\attacker.example\\track.png">',
        '<a href="\\/attacker.example/phish">click</a>',
    ],
)
def test_sanitizer_drops_authority_steering_urls(attribute):
    clean = sanitize_rich_html(attribute)
    lowered = clean.lower()
    assert "attacker.example" not in lowered
    assert "href=" not in lowered and "src=" not in lowered
