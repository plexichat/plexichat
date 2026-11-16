"""Boundary tests for the canonical security-header policy (commit 4)."""

from src.api.middleware.security_header_policy import (
    CANONICAL_HEADERS,
    canonical_security_headers,
    is_weakened,
    merge_security_headers,
)


class TestIsWeakened:
    def test_nosniff_required(self):
        assert is_weakened(b"X-Content-Type-Options", b"sniff")
        assert not is_weakened(b"X-Content-Type-Options", b"nosniff")

    def test_referrer_policy(self):
        assert is_weakened(b"Referrer-Policy", b"unsafe-url")
        assert not is_weakened(b"Referrer-Policy", b"no-referrer")

    def test_frame_options(self):
        assert is_weakened(b"X-Frame-Options", b"ALLOWALL")
        assert not is_weakened(b"X-Frame-Options", b"DENY")


class TestCanonical:
    def test_contains_all_required(self):
        headers = dict(canonical_security_headers())
        assert headers[b"X-Content-Type-Options"] == b"nosniff"
        assert headers[b"Referrer-Policy"] == b"strict-origin-when-cross-origin"
        assert headers[b"X-Frame-Options"] == b"SAMEORIGIN"

    def test_no_frame_when_disabled(self):
        names = [n for n, _ in canonical_security_headers(include_frame=False)]
        assert b"X-Frame-Options" not in names


class TestMerge:
    def test_adds_missing(self):
        merged = merge_security_headers([])
        names = {n.lower() for n, _ in merged}
        assert set(CANONICAL_HEADERS) <= names

    def test_replaces_weakened(self):
        merged = merge_security_headers([(b"X-Content-Type-Options", b"sniff")])
        vals = {n: v for n, v in merged}
        assert vals[b"X-Content-Type-Options"] == b"nosniff"
        # no duplicate introduced
        assert sum(1 for n, _ in merged if n == b"X-Content-Type-Options") == 1

    def test_preserves_stronger(self):
        merged = merge_security_headers([(b"Referrer-Policy", b"no-referrer")])
        vals = {n: v for n, v in merged}
        assert vals[b"Referrer-Policy"] == b"no-referrer"

    def test_case_insensitive(self):
        merged = merge_security_headers([(b"x-frame-options", b"ALLOWALL")])
        vals = {n.lower(): v for n, v in merged}
        assert vals[b"x-frame-options"] == b"SAMEORIGIN"

    def test_media_still_corrects_allowall(self):
        merged = merge_security_headers(
            [(b"X-Frame-Options", b"ALLOWALL")], include_frame=False
        )
        vals = {n.lower(): v for n, v in merged}
        assert vals[b"x-frame-options"] == b"SAMEORIGIN"
