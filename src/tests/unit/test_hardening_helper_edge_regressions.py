"""Regressions for edge cases the shared hardening helpers used to accept.

These pin three high-leverage invariants:

* an identifier is *exactly* ``[A-Za-z_][A-Za-z0-9_]*`` (Python's ``$`` anchor
  also matches before a final newline, which let one into SQL statements);
* a non-integral number is refused instead of being truncated into range (and
  non-finite/bool values raise ``HardeningViolation``, not a bare exception);
* a URL containing C0/DEL controls is refused before it can be parsed or stored.
"""

import pytest

from src.core.security.errors import OutboundUrlRejected
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    is_identifier,
    parse_id,
    require_http_url,
    require_https_url,
    require_identifier,
    require_ws_url,
)
from src.core.security.outbound import parse_outbound_url


class TestIdentifierNewlineBypass:
    @pytest.mark.parametrize(
        "name",
        [
            "users\n",
            "a1\n",
            "_x\n",
            "users\r\n",
            "users\x00",
        ],
    )
    def test_trailing_control_byte_is_not_an_identifier(self, name):
        # re.match(r"...$", "users\n") used to succeed; the newline then flowed
        # into f"SELECT ... FROM {name}" / PRAGMA / DROP statements.
        assert not is_identifier(name)
        with pytest.raises(HardeningViolation):
            require_identifier(name)

    def test_plain_identifier_still_accepted(self):
        assert is_identifier("msg_messages_2")
        assert require_identifier("msg_messages_2") == "msg_messages_2"


class TestNumberTruncationBoundsBypass:
    @pytest.mark.parametrize(
        "value",
        [10.9, 0.9, 3.7, -0.5, -0.99, float("inf"), float("-inf"), float("nan")],
    )
    def test_non_integral_or_non_finite_is_refused(self, value):
        # int(10.9) == 10 satisfied maximum=10; int(-0.5) == 0 satisfied
        # minimum=0. Neither may be quieted into the allowed range.
        with pytest.raises(HardeningViolation):
            bounded_int(value, "x", minimum=0, maximum=10)

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_is_not_an_integer_here(self, value):
        with pytest.raises(HardeningViolation):
            bounded_int(value, "x", minimum=0, maximum=10)

    def test_fractional_id_is_refused(self):
        with pytest.raises(HardeningViolation):
            parse_id(1.5, "id")

    def test_integral_float_and_strings_still_accepted(self):
        assert bounded_int(7.0, "x", minimum=0, maximum=10) == 7
        assert bounded_int("42", "x", minimum=0, maximum=100) == 42
        assert parse_id("123") == 123


class TestUrlControlCharacters:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/\r\nX-Injected: 1",
            "https://example.com\n.evil.test",
            "https://exa\tmple.test/path",
            "https://example.test/\x00hidden",
            "wss://relay.test/\r\nX: 1",
            "\thttps://example.test",
        ],
    )
    def test_shape_validators_refuse_control_characters(self, url):
        for validator in (require_http_url, require_https_url, require_ws_url):
            with pytest.raises(HardeningViolation):
                validator(url, "endpoint")

    def test_malformed_ipv6_raises_hardening_violation(self):
        # urlparse(...).hostname raises a bare ValueError for a broken literal;
        # the helper must keep its documented exception type.
        with pytest.raises(HardeningViolation):
            require_https_url("https://[::1", "endpoint")

    def test_ordinary_urls_unchanged(self):
        assert require_https_url(" https://example.test/path ") == (
            "https://example.test/path"
        )
        assert require_http_url("http://example.test") == "http://example.test"
        assert require_ws_url("wss://relay.test/socket") == "wss://relay.test/socket"

    def test_outbound_gate_refuses_control_characters_before_dns(self):
        # The control check must fire before any resolution so this stays a
        # pure unit test and no network access is attempted.
        with pytest.raises(OutboundUrlRejected):
            parse_outbound_url(
                "https://example.test/path\r\nInjected: yes", "remote_url"
            )
