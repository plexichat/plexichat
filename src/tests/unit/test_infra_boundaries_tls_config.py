"""Boundary tests for TLS/pinning config string validation (commit 7)."""

import pytest

from src.core.tls.config_validation import (
    MAX_HOSTNAME_LENGTH,
    MAX_PINS,
    TLSConfigValidationError,
    cap_config_string,
    validate_fingerprint,
    validate_hostname,
    validate_pin_list,
    validate_san_entry,
)

FP = "a" * 64


class TestCapString:
    def test_valid(self):
        assert cap_config_string("hello", "x") == "hello"

    @pytest.mark.parametrize("bad", ["", "   ", None, 123, "a\x00b", "a\r\nb"])
    def test_invalid(self, bad):
        with pytest.raises(TLSConfigValidationError):
            cap_config_string(bad, "x")

    def test_length(self):
        with pytest.raises(TLSConfigValidationError):
            cap_config_string("a" * 2000, "x")


class TestHostname:
    @pytest.mark.parametrize(
        "ok", ["localhost", "example.com", "a.b.c", "xn--bcher-kva.de"]
    )
    def test_valid(self, ok):
        assert validate_hostname(ok) == ok

    @pytest.mark.parametrize("bad", ["-bad.com", "bad-.com", "a..b", "a b", "_x.com"])
    def test_invalid_labels(self, bad):
        with pytest.raises(TLSConfigValidationError):
            validate_hostname(bad)

    def test_too_long(self):
        with pytest.raises(TLSConfigValidationError):
            validate_hostname("a" * (MAX_HOSTNAME_LENGTH + 1))


class TestSanEntry:
    def test_ipv4(self):
        assert validate_san_entry("127.0.0.1") == "127.0.0.1"

    def test_ipv6(self):
        assert validate_san_entry("::1") == "::1"

    def test_dns(self):
        assert validate_san_entry("example.com") == "example.com"


class TestFingerprint:
    def test_valid(self):
        assert validate_fingerprint(FP) == FP

    def test_colon_separated(self):
        colon = ":".join(FP[i : i + 2] for i in range(0, 64, 2))
        assert validate_fingerprint(colon) == FP

    @pytest.mark.parametrize(
        "bad", ["abc", "z" * 64, "a" * 63, "a" * 65, "a\r\n" + "b" * 64]
    )
    def test_invalid(self, bad):
        with pytest.raises(TLSConfigValidationError):
            validate_fingerprint(bad)


class TestPinList:
    def test_bounded(self):
        pins = [str(i).zfill(2) * 32 for i in range(64)]
        with pytest.raises(TLSConfigValidationError):
            validate_pin_list(pins)
        assert len(validate_pin_list(pins[:MAX_PINS])) == MAX_PINS

    def test_empty(self):
        assert validate_pin_list([]) == []
