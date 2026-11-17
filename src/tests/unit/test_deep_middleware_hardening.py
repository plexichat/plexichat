"""Deep hardening regressions for the middleware boundary helpers.

Pins three security invariants:

1. IPv4-mapped IPv6 addresses must not bypass IPv4 allow/deny rules.
2. Every canonical security header -- including X-XSS-Protection and
   X-Permitted-Cross-Domain-Policies -- must be corrected when a handler
   emits a weakened value, and the audit must flag it.
3. The perimeter IP-block 403 must carry the canonical security policy even
   though it short-circuits outside the security-headers middleware.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.api.middleware.ip_validation import (
    ip_in_rules,
    is_ip_permitted,
    parse_rule_list,
)
from src.api.middleware.security_header_audit import audit_security_headers
from src.api.middleware.security_header_policy import (
    is_weakened,
    merge_security_headers,
)


class TestIpv4MappedCidrBypass:
    """::ffff:a.b.c.d is the same host as a.b.c.d and must not dodge a rule."""

    def test_mapped_client_matches_ipv4_deny_rule(self):
        deny = parse_rule_list(["192.168.1.0/24"])
        assert ip_in_rules("::ffff:192.168.1.10", deny) is True
        assert is_ip_permitted("::ffff:192.168.1.10", [], deny) is False

    def test_mapped_client_matches_ipv4_allow_rule(self):
        allow = parse_rule_list(["10.0.0.0/8"])
        assert ip_in_rules("::ffff:10.1.2.3", allow) is True

    def test_unrelated_ipv6_still_does_not_match_ipv4_rule(self):
        deny = parse_rule_list(["192.168.1.0/24"])
        assert ip_in_rules("2001:db8::10", deny) is False
        assert ip_in_rules("::1", deny) is False

    def test_mapped_loopback_matches_ipv4_loopback_deny(self):
        deny = parse_rule_list(["127.0.0.0/8"])
        assert ip_in_rules("::ffff:127.0.0.1", deny) is True


class TestWeakenedSecurityHeadersCorrected:
    """Every canonical header must be enforced, not just three of five."""

    def test_xss_protection_zero_is_weakened_and_replaced(self):
        assert is_weakened(b"X-XSS-Protection", b"0") is True
        merged = dict(merge_security_headers([(b"X-XSS-Protection", b"0")]))
        assert merged[b"X-XSS-Protection"] == b"1; mode=block"

    def test_xss_protection_strong_value_is_preserved(self):
        assert is_weakened(b"X-XSS-Protection", b"1; mode=block") is False
        merged = dict(
            merge_security_headers([(b"X-XSS-Protection", b"1; MODE=BLOCK")])
        )
        assert merged[b"X-XSS-Protection"] == b"1; MODE=BLOCK"

    def test_permitted_cross_domain_all_is_weakened_and_replaced(self):
        assert is_weakened(b"X-Permitted-Cross-Domain-Policies", b"all") is True
        merged = dict(
            merge_security_headers([(b"X-Permitted-Cross-Domain-Policies", b"all")])
        )
        assert merged[b"X-Permitted-Cross-Domain-Policies"] == b"none"

    def test_audit_flags_both_weakened_headers(self):
        issues = audit_security_headers(
            [
                (b"X-Content-Type-Options", b"nosniff"),
                (b"Referrer-Policy", b"no-referrer"),
                (b"X-Frame-Options", b"DENY"),
                (b"X-XSS-Protection", b"0"),
                (b"X-Permitted-Cross-Domain-Policies", b"all"),
            ]
        )
        assert "weakened:x-xss-protection" in issues
        assert "weakened:x-permitted-cross-domain-policies" in issues


class TestIpBlockForbiddenCarriesSecurityHeaders:
    """A perimeter 403 must not be a security-header-free response."""

    def _run(self, path="/api/v1/users"):
        from src.api.middleware.ip_blocking import IPBlockingMiddleware

        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def inner(scope, receive_, send_):
            await send_({"type": "http.response.start", "status": 200, "headers": []})
            await send_({"type": "http.response.body", "body": b"ok"})

        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"host", b"testserver")],
            "client": ("203.0.113.9", 1234),
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
        fake_auth = MagicMock()
        fake_auth.is_ip_blocked.return_value = True
        cfg = SimpleNamespace(
            api_prefix="/api/v1", cors_origins=(), cors_allow_credentials=True
        )
        with (
            patch("src.api.get_auth", return_value=fake_auth),
            patch("src.api.config.get_api_config", return_value=cfg),
        ):
            import asyncio

            asyncio.run(IPBlockingMiddleware(inner)(scope, receive, send))
        return messages

    def test_forbidden_response_is_stamped(self):
        messages = self._run()
        start = next(m for m in messages if m["type"] == "http.response.start")
        assert start["status"] == 403
        headers = {k.lower(): v for k, v in start["headers"]}
        assert headers.get(b"x-content-type-options") == b"nosniff"
        assert headers.get(b"referrer-policy") == b"strict-origin-when-cross-origin"
        assert headers.get(b"x-frame-options") == b"SAMEORIGIN"
        assert b"content-security-policy" in headers
        assert b"strict-transport-security" in headers
        assert b"permissions-policy" in headers

    def test_forbidden_response_has_no_reflected_cors_headers(self):
        messages = self._run()
        start = next(m for m in messages if m["type"] == "http.response.start")
        names = [k.lower() for k, _ in start["headers"]]
        assert b"access-control-allow-origin" not in names
