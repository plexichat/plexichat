"""Boundary tests for security-header auditing (theme 4 follow-up)."""

from typing import cast

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
                bound_header_count([], limit=cast(int, bad))
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected ValueError for {bad}")

    def test_security_headers_survive_when_over_limit(self):
        # Regression: security headers are appended at the END of the response
        # header list. The old "keep first N" bound silently dropped every one
        # of them once a handler emitted >= limit ordinary headers, producing a
        # response with no CSP/HSTS/nosniff/frame policy at all.
        ordinary = [(f"x-upstream-{i}".encode(), b"v") for i in range(260)]
        security = [
            (b"X-Content-Type-Options", b"nosniff"),
            (b"Referrer-Policy", b"strict-origin-when-cross-origin"),
            (b"X-Frame-Options", b"SAMEORIGIN"),
            (b"X-XSS-Protection", b"1; mode=block"),
            (b"X-Permitted-Cross-Domain-Policies", b"none"),
            (b"Content-Security-Policy", b"default-src 'none'"),
            (b"Strict-Transport-Security", b"max-age=31536000"),
            (b"Permissions-Policy", b"camera=()"),
        ]
        out = bound_header_count(ordinary + security, limit=200)
        names = {name.lower() for name, _ in out}
        assert len(out) == 200
        for name, _ in security:
            assert name.lower() in names

    def test_enforce_response_policy_keeps_headers_under_flood(self):
        # End-to-end: a handler flooding distinct headers must not strip the
        # canonical security policy that enforce_response_policy stamps.
        from src.api.middleware.response_policy import enforce_response_policy

        flooded = [(f"x-upstream-{i}".encode(), b"v") for i in range(260)]
        out = enforce_response_policy(
            flooded, path="/api/v1/users", api_prefix="/api/v1"
        )
        names = {name.lower() for name, _ in out}
        for required in (
            b"x-content-type-options",
            b"referrer-policy",
            b"x-frame-options",
            b"x-xss-protection",
            b"x-permitted-cross-domain-policies",
            b"content-security-policy",
            b"strict-transport-security",
            b"permissions-policy",
        ):
            assert required in names


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
