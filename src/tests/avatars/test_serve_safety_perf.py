"""Regression tests for the single-pass avatar serve-safety classifier.

The route used to deep-scan a stored avatar up to five times per GET (markup,
polyglot, active content, svg-guard re-check, plus the manager's own scan).
These tests pin both the *result* equivalence with the legacy checks and the
new property that a payload is scanned exactly once.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.api.routes.avatars import validation
from src.core.avatars.manager import upload_guard

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
GIF = b"GIF89a" + b"\x00" * 128
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 128
SVG_SCRIPT = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
SPOOFED = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16 + b"<script>alert(1)</script>"
POLYGLOT = b"GIF89a" + b"\x00" * 16 + b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    "ctype,payload,expected",
    [
        ("image/png", PNG, True),
        ("image/gif", GIF, True),
        ("image/jpeg", JPEG, True),
        ("image/svg+xml", SVG_SCRIPT, False),
        ("image/png", SPOOFED, False),
        ("image/gif", POLYGLOT, False),
        ("text/html", b"<html></html>", False),
    ],
)
def test_single_pass_matches_layered_guards(ctype, payload, expected):
    """The shared classifier agrees with the layered fail-closed guards."""
    assert upload_guard.serve_payload_is_safe(ctype, payload) is expected
    # The route-level helper must produce the same verdict without a checksum.
    assert validation.is_serve_safe(ctype, payload) is expected


def test_serve_payload_scans_head_once():
    """A benign payload is normalised once, not once per individual guard."""
    real_head = upload_guard._head
    calls = []

    def _spy(data, limit=upload_guard._SCAN_LIMIT):
        calls.append(len(data))
        return real_head(data, limit)

    with patch.object(upload_guard, "_head", side_effect=_spy):
        assert upload_guard.serve_payload_is_safe("image/png", PNG) is True
    assert len(calls) == 1


def test_verdict_cache_avoids_repeated_deep_scans():
    """Repeated GETs of an unchanged avatar reuse the checksum-keyed verdict."""
    validation._serve_verdict_cache.clear()
    with patch.object(
        validation, "serve_payload_is_safe", return_value=True
    ) as classifier:
        assert validation.is_serve_safe("image/png", PNG, "checksum-a") is True
        assert validation.is_serve_safe("image/png", PNG, "checksum-a") is True
        assert classifier.call_count == 1
        # A different stored payload must be inspected independently.
        assert validation.is_serve_safe("image/png", PNG, "checksum-b") is True
        assert classifier.call_count == 2
    validation._serve_verdict_cache.clear()


def test_verdict_cache_is_keyed_by_content_type():
    """Identical bytes under a blocked type must not inherit a cached pass."""
    validation._serve_verdict_cache.clear()
    try:
        assert validation.is_serve_safe("image/png", PNG, "same-bytes") is True
        assert validation.is_serve_safe("text/html", PNG, "same-bytes") is False
    finally:
        validation._serve_verdict_cache.clear()
