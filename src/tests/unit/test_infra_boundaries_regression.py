"""Cross-cutting regression tests for the infrastructure boundary campaign.

Where the theme-specific files exercise one module each, this file pins the
*interaction* invariants that matter once the helpers are used together: a
hostile value must end up either rejected or reduced to something bounded and
control-free, no matter which layer it passes through. New helpers added to
this campaign should get a case here so the boundary surface stays honest.
"""

from __future__ import annotations

import pytest

from src.api.middleware.dependency_validation import (
    bounded_id,
    bounded_limit,
    bounded_page,
)
from src.api.middleware.header_validation import (
    HeaderValidationError,
    safe_header_value,
    validate_header_value,
)
from src.api.middleware.ip_validation import (
    IPRuleError,
    is_ip_permitted,
    parse_cidr,
    parse_rule_list,
)
from src.api.middleware.security_header_policy import merge_security_headers
from src.core.ratelimit.config_validation import coerce_config_int
from src.core.ratelimit.manager.numeric_guards import (
    guard_effective_limit,
    safe_multiply,
)
from src.core.ratelimit.manager.validation import validate_config
from src.core.ratelimit.models import RateLimitConfig
from src.core.security.audit import (
    REDACTED,
    bound_field,
    safe_log_line,
    sanitise_audit_entry,
)
from src.core.security.hardening import HardeningViolation
from src.core.tls.config_validation import (
    TLSConfigValidationError,
    validate_fingerprint,
    validate_hostname,
)
from src.core.tls.path_validation import TLSPathError, validate_cert_path


CONTROL_PAYLOADS = [
    "a\r\nX-Injected: 1",
    "a\nb",
    "a\rb",
    "a\x00b",
    "a\x7fb",
]


class TestControlCharactersNeverEscape:
    """A control-char payload must be rejected or stripped at every boundary."""

    @pytest.mark.parametrize("payload", CONTROL_PAYLOADS)
    def test_header_value_rejects(self, payload):
        with pytest.raises(HeaderValidationError):
            validate_header_value(payload)
        assert "\n" not in safe_header_value(payload)

    @pytest.mark.parametrize("payload", CONTROL_PAYLOADS)
    def test_audit_field_strips(self, payload):
        assert not any(c in bound_field(payload) for c in "\r\n\x00\x7f")

    @pytest.mark.parametrize("payload", CONTROL_PAYLOADS)
    def test_log_line_is_single_line(self, payload):
        line = safe_log_line("event", payload)
        assert "\n" not in line and "\r" not in line

    @pytest.mark.parametrize("payload", CONTROL_PAYLOADS)
    def test_ip_entry_rejects(self, payload):
        with pytest.raises(IPRuleError):
            parse_cidr(payload)

    @pytest.mark.parametrize("payload", CONTROL_PAYLOADS)
    def test_tls_config_rejects(self, payload):
        with pytest.raises(TLSConfigValidationError):
            validate_hostname(payload)


class TestNumericBoundariesTogether:
    def test_config_rejection_chains_to_manager(self):
        # A bad raw config is coerced to a default that the manager accepts.
        coerced = coerce_config_int(-5, "requests", minimum=0, maximum=100, default=10)
        cfg = RateLimitConfig(requests=coerced, window_seconds=60.0, burst=0)
        assert validate_config(cfg) is cfg

    def test_effective_limit_guard(self):
        assert guard_effective_limit(10, 5) == 15
        with pytest.raises(ValueError):
            guard_effective_limit(2**80, 5)

    def test_multiplier_guard(self):
        assert safe_multiply(10, 2) == 20
        with pytest.raises(ValueError):
            safe_multiply(10, 10**30)

    def test_dependency_params(self):
        assert bounded_id("123") == 123
        assert bounded_page("2") == 2
        assert bounded_limit("25") == 25
        for bad in (0, -1, 2**70):
            with pytest.raises(HardeningViolation):
                bounded_id(bad)


class TestSecurityHeadersEndToEnd:
    def test_injected_header_cannot_weaken_policy(self):
        # Even if an upstream handler tries to set a weak header, the merge
        # step restores the canonical value.
        merged = dict(
            merge_security_headers(
                [
                    (b"X-Content-Type-Options", b"sniff"),
                    (b"Referrer-Policy", b"unsafe-url"),
                ]
            )
        )
        assert merged[b"X-Content-Type-Options"] == b"nosniff"
        assert merged[b"Referrer-Policy"] == b"strict-origin-when-cross-origin"

    def test_no_control_characters_in_final_headers(self):
        for name, value in merge_security_headers([]):
            assert not any(c in value for c in b"\r\n\x00")


class TestTlsAndIpBoundaries:
    def test_certificate_path_confined(self, tmp_path):
        base = tmp_path / "certs"
        base.mkdir()
        cert = base / "server.crt"
        cert.write_text("x")
        assert validate_cert_path(str(cert), allowed_roots=[str(base)])

        outside = tmp_path / "outside.crt"
        outside.write_text("x")
        with pytest.raises(TLSPathError):
            validate_cert_path(str(outside), allowed_roots=[str(base)])

    def test_pin_format(self):
        fp = "b" * 64
        assert validate_fingerprint(fp) == fp
        with pytest.raises(TLSConfigValidationError):
            validate_fingerprint("not-a-pin")

    def test_ip_access_decision(self):
        allow = parse_rule_list(["10.0.0.0/8"])
        deny = parse_rule_list(["10.0.0.9"])
        assert is_ip_permitted("10.0.0.8", allow, deny)
        assert not is_ip_permitted("10.0.0.9", allow, deny)
        assert not is_ip_permitted("8.8.8.8", allow, deny, default_allow=False)


class TestAuditRedaction:
    def test_secrets_never_appear(self):
        record = sanitise_audit_entry(
            {
                "actor": "alice",
                "authorization": "Bearer abcdefghijklmnop",
                "note": "token=supersecretvalue",
            }
        )
        blob = repr(record)
        assert REDACTED in blob
        assert "abcdefghijklmnop" not in blob
        assert "supersecretvalue" not in blob

    def test_control_and_length_bounds(self):
        record = sanitise_audit_entry(
            {"path": "/a\r\nb/" + "x" * 5000}, max_value_length=100
        )
        assert "\n" not in record["path"]
        assert len(record["path"]) < 200
