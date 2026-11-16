"""Boundary tests for security-header auditing (theme 4 follow-up)."""

from src.api.middleware.security_header_audit import (
    audit_security_headers,
    bound_header_count,
    dedupe_headers,
    is_unsafe_header,
    strip_unsafe_headers,
)


class TestUnsafe:
    def test_nosniff_required(self):
        assert is_unsafe_header(b"X-Content-Type-Options", b"sniff")
        assert not is_unsafe_header(b"X-Content-Type-Options", b"nosniff")

    def test_permissive_frame(self):
        assert is_unsafe_header(b"X-Frame-Options", b"ALLOWALL")

    def test_strip(self):
        out = strip_unsafe_headers(
            [(b"X-Content-Type-Options", b"sniff"), (b"X-Frame-Options", b"SAMEORIGIN")]
        )
        assert out == [(b"X-Frame-Options", b"SAMEORIGIN")]


class TestDedupe:
    def test_keeps_last(self):
        out = dedupe_headers([(b"X-A", b"1"), (b"X-A", b"2")])
        assert out == [(b"X-A", b"2")]

    def test_case_insensitive(self):
        out = dedupe_headers([(b"X-A", b"1"), (b"x-a", b"2")])
        assert len(out) == 1


class TestBoundCount:
    def test_caps(self):
        headers = [(f"X-{i}".encode(), b"v") for i in range(10)]
        assert len(bound_header_count(headers, limit=3)) == 3

    def test_bad_limit(self):
        for bad in (0, -1, True, 1.5):
            try:
                bound_header_count([], limit=bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected ValueError for {bad}")


class TestAudit:
    def test_missing_all(self):
        issues = audit_security_headers([])
        assert any(i.startswith("missing:") for i in issues)

    def test_weakened(self):
        issues = audit_security_headers([(b"Referrer-Policy", b"unsafe-url")])
        assert "weakened:referrer-policy" in issues

    def test_clean(self):
        headers = [
            (b"X-Content-Type-Options", b"nosniff"),
            (b"Referrer-Policy", b"no-referrer"),
            (b"X-Frame-Options", b"DENY"),
            (b"X-XSS-Protection", b"1; mode=block"),
            (b"X-Permitted-Cross-Domain-Policies", b"none"),
        ]
        assert audit_security_headers(headers) == []
