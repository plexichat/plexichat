"""Regression: active markup / script tokens smuggled into browser-renderable
media uploads must be rejected regardless of the declared content type.

``FileValidator`` only routed payloads to the defusedxml SVG sanitizer when
the *declared* type was ``image/svg+xml``. A polyglot such as PNG magic bytes
followed by ``<svg onload=...>`` declared as ``image/png`` therefore passed
the magic-byte check and was stored verbatim. Avatars, emojis, stickers and
soundboard sounds already ran a whole-payload active-content guard; the main
media upload pipeline did not.

The guard is deliberately limited to browser-renderable types so inert
downloads (``text/plain`` etc.) can still legitimately contain markup text.
It also avoids binary-container "polyglot" heuristics whose short signatures
(ID3/WEBP) occur by chance in real compressed images and would reject them.
"""

import pytest

from src.core.media.security.validation import (
    FileValidator,
    contains_active_markup,
    is_browser_renderable,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff\xe0"


def _filename(declared_type: str) -> str:
    return {"image/png": "x.png", "image/jpeg": "x.jpg"}.get(declared_type, "x.dat")


@pytest.mark.parametrize(
    "payload, declared_type",
    [
        (PNG_MAGIC + b"\x00" * 32 + b"<svg onload=alert(1)></svg>", "image/png"),
        (JPEG_MAGIC + b"\x00" * 32 + b"<script>alert(1)</script>", "image/jpeg"),
        (PNG_MAGIC + b"\x00" * 8 + b"javascript:alert(1)", "image/png"),
        (PNG_MAGIC + b"\x00" * 8 + b"<iframe src=javascript:alert(1)>", "image/png"),
        (PNG_MAGIC + b"\x00" * 8 + b"onerror\t=alert(1)", "image/png"),
        (
            PNG_MAGIC + b"\x00" * 8 + b"<foreignObject><script/></foreignObject>",
            "image/png",
        ),
    ],
)
def test_active_markup_polyglot_rejected_for_browser_types(payload, declared_type):
    valid, error, _ = FileValidator().validate_file(
        payload, _filename(declared_type), declared_type
    )
    assert valid is False
    assert "active or markup" in (error or "").lower()


@pytest.mark.parametrize(
    "payload, declared_type",
    [
        (PNG_MAGIC + b"\x00" * 64, "image/png"),
        (JPEG_MAGIC + b"\x00" * 64, "image/jpeg"),
    ],
)
def test_clean_media_bytes_are_still_accepted(payload, declared_type):
    valid, error, _ = FileValidator().validate_file(
        payload, _filename(declared_type), declared_type
    )
    assert valid is True, error


def test_markup_text_in_download_only_type_is_not_rejected():
    """Inert document uploads are served as downloads and may contain markup."""
    payload = b"notes: <script>this is documentation</script>"
    valid, error, _ = FileValidator().validate_file(payload, "x.txt", "text/plain")
    assert valid is True, error


def test_guard_helpers():
    assert is_browser_renderable("image/png")
    assert is_browser_renderable("VIDEO/mp4")
    assert not is_browser_renderable("text/plain")
    assert not is_browser_renderable("application/octet-stream")
    assert contains_active_markup(b"\x89PNG\r\n\x1a\n" + b"<svg onload=x>")
    assert contains_active_markup(b"<img onerror =alert(1)>")
    assert not contains_active_markup(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)


def test_declared_svg_still_uses_defusedxml_sanitizer():
    """The existing SVG path remains in force for honestly-labelled SVG."""
    valid, error, _ = FileValidator().validate_file(
        b"<svg onload=alert(1)></svg>", "x.svg", "image/svg+xml"
    )
    assert valid is False
    assert "SVG" in (error or "")
