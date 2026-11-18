"""Unit tests for browser-normalisation-aware media URL scheme guarding."""

import pytest

from src.core.avatars.manager.url_guard import (
    is_safe_media_url,
    normalized_url,
    url_scheme,
)


@pytest.mark.parametrize(
    "raw, expected_scheme",
    [
        ("https://cdn/x.mp3", "https"),
        ("http://cdn/x.mp3", "http"),
        ("javascript:alert(1)", "javascript"),
        ("javascript&#58;alert(1)", "javascript"),
        ("javascript&#x3a;alert(1)", "javascript"),
        ("javascript&colon;alert(1)", "javascript"),
        ("&#106;avascript:alert(1)", "javascript"),
        ("java\nscript:alert(1)", "javascript"),
        ("java\tscript:alert(1)", "javascript"),
        ("\x01javascript:alert(1)", "javascript"),
        ("data:text/html,x", "data"),
        ("data&#58;text/html,x", "data"),
        ("/media/x.mp3", None),
        ("cdn.example.com/x.mp3", None),
    ],
)
def test_url_scheme_uses_browser_normalized_form(raw, expected_scheme):
    assert url_scheme(raw) == expected_scheme


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "javascript&#58;alert(1)",
        "javascript&#x3a;alert(1)",
        "javascript&colon;alert(1)",
        "&#106;avascript:alert(1)",
        "java\nscript:alert(1)",
        "java\tscript:alert(1)",
        "\x01javascript:alert(1)",
        "data:text/html,x",
        "data&#58;text/html,x",
        "file&#58:///etc/passwd",
        "vbscript&#58;msgbox(1)",
        "//evil.example/x.mp3",
        "/\\evil.example/x.mp3",
    ],
)
def test_unsafe_media_urls_rejected(raw):
    assert is_safe_media_url(raw) is False


@pytest.mark.parametrize(
    "raw",
    [
        "https://cdn.example.com/sounds/x.mp3",
        "HTTPS://cdn.example.com/sounds/x.mp3",
        "http://cdn.example.com/sounds/x.ogg",
        "/media/uploads/2026/x.wav",
        "cdn.example.com/x.mp3",
    ],
)
def test_safe_media_urls_accepted(raw):
    assert is_safe_media_url(raw) is True


def test_relative_disallowed_when_requested():
    assert is_safe_media_url("/media/x.mp3", allow_relative=False) is False
    assert is_safe_media_url("https://cdn/x.mp3", allow_relative=False) is True


def test_normalized_url_does_not_mutate_source():
    raw = "javascript&#58;alert(1)"
    assert normalized_url(raw) == "javascript:alert(1)"
    assert raw == "javascript&#58;alert(1)"
