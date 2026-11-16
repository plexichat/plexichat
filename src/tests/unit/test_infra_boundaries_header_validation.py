"""Boundary tests for middleware header-value validation (commit 3)."""

import pytest

from src.api.middleware.header_validation import (
    MAX_HEADER_VALUE_LENGTH,
    HeaderValidationError,
    safe_header_value,
    validate_header_name,
    validate_header_value,
    validated_headers,
)


class TestHeaderValue:
    @pytest.mark.parametrize(
        "bad",
        ["a\r\nX-Evil: 1", "a\nb", "a\rb", "a\x00b", "a\x7fb", None],
    )
    def test_control_characters_rejected(self, bad):
        with pytest.raises(HeaderValidationError):
            validate_header_value(bad)

    def test_normal_value(self):
        assert validate_header_value("application/json") == "application/json"

    def test_length_capped(self):
        validate_header_value("a" * MAX_HEADER_VALUE_LENGTH)
        with pytest.raises(HeaderValidationError):
            validate_header_value("a" * (MAX_HEADER_VALUE_LENGTH + 1))

    def test_custom_max_length(self):
        with pytest.raises(HeaderValidationError):
            validate_header_value("abc", max_length=2)

    @pytest.mark.parametrize("bad", [0, -1, True, 1.5])
    def test_bad_max_length(self, bad):
        with pytest.raises(HeaderValidationError):
            validate_header_value("x", max_length=bad)


class TestHeaderName:
    @pytest.mark.parametrize("ok", ["X-Request-Id", "content-type", "X_Token"])
    def test_valid(self, ok):
        assert validate_header_name(ok) == ok

    @pytest.mark.parametrize("bad", ["", "X Bad", "X:Evil", "a\r\nb", 12])
    def test_invalid(self, bad):
        with pytest.raises(HeaderValidationError):
            validate_header_name(bad)

    def test_too_long(self):
        with pytest.raises(HeaderValidationError):
            validate_header_name("a" * 200)


class TestSafeHeaderValue:
    def test_drops_controls(self):
        assert safe_header_value("a\r\nb") == "ab"

    def test_fallback_when_only_controls(self):
        assert safe_header_value("\r\n", fallback="none") == "none"

    def test_truncates(self):
        assert len(safe_header_value("a" * 5000, max_length=50)) == 50


class TestValidatedHeaders:
    def test_roundtrip(self):
        out = validated_headers([(b"X-Test", b"abc")])
        assert out == [(b"X-Test", b"abc")]

    def test_rejects_injected_value(self):
        with pytest.raises(HeaderValidationError):
            validated_headers([(b"X-Test", b"a\r\nX-Evil: 1")])
