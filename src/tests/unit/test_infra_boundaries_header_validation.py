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

    @pytest.mark.parametrize(
        "bad",
        [
            "X-Evil\n",  # ``$`` matches before a trailing newline with re.match
            "X-Evil\r",
            "X-Evil\x00",
            "X-Evil\u0085",  # NEL is a line break for many proxies/loggers
            "X-Evil\u2028",
            "X-Evil\nInjected: 1",
        ],
    )
    def test_trailing_control_cannot_smuggle_into_header_name(self, bad):
        """A header name must never carry CR/LF/NEL (header injection)."""
        with pytest.raises(HeaderValidationError):
            validate_header_name(bad)

    def test_validated_headers_rejects_injected_name(self):
        """The collection helper must not emit a name with an embedded line break."""
        with pytest.raises(HeaderValidationError):
            validated_headers([(b"X-Evil\n", b"1")])

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

    @pytest.mark.parametrize(
        "separator",
        [
            "\u0085",  # NEL: a line break for many log shippers and proxies
            "\u2028",  # Unicode LINE SEPARATOR
            "\u2029",  # Unicode PARAGRAPH SEPARATOR
            "\u2066",  # bidi isolate: renders the value as something else
            "\ufeff",  # BOM
        ],
    )
    def test_fallback_strips_full_control_set(self, separator):
        """The safe fallback must never return characters the validator rejects.

        ``safe_header_value`` degrades to a stripping fallback when the value
        is invalid. That fallback used a narrow ``[\\x00-\\x1f\\x7f]`` regex,
        so a value carrying NEL/U+2028/U+2029/bidi controls passed through as
        "safe" even though ``validate_header_value`` (and the shared hardening
        primitive) treat them as header-splitting/spoofing vectors. An ASGI
        server or downstream proxy that normalises those characters to a
        newline can then split the header.
        """
        with pytest.raises(HeaderValidationError):
            validate_header_value("ok" + separator + "X-Evil: 1")

        cleaned = safe_header_value("ok" + separator + "X-Evil: 1")
        assert separator not in cleaned
        assert cleaned == "okX-Evil: 1"
        # Whatever the fallback returns must itself satisfy the validator.
        assert validate_header_value(cleaned) == cleaned


class TestValidatedHeaders:
    def test_roundtrip(self):
        out = validated_headers([(b"X-Test", b"abc")])
        assert out == [(b"X-Test", b"abc")]

    def test_rejects_injected_value(self):
        with pytest.raises(HeaderValidationError):
            validated_headers([(b"X-Test", b"a\r\nX-Evil: 1")])
