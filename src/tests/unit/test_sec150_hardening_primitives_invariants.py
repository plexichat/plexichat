"""SEC150 invariant: hardening primitives reject control chars / oversized input.

The helpers in :mod:`src.core.security.hardening` are the single choke point for
values that are interpolated into HTTP headers, SQL statements, filesystem paths
and integer literals. Each helper exists because a call site once trusted an
untrusted value there. This suite pins the rejection (or, for the sanitizer, the
scrubbing) of the two payload classes that keep recurring in findings:

* embedded C0/DEL control characters (header splitting, log forging, statement
  injection via a trailing newline);
* values outside the helper's declared size/range (oversized names, ids and
  integers).

The public surface is imported from the package (not a submodule) on purpose: a
helper that stops being re-exported is itself a regression.
"""

import math

import pytest

from src.core.security.hardening import (
    SNOWFLAKE_MAX,
    HardeningViolation,
    bounded_int,
    content_disposition,
    is_identifier,
    parse_id,
    reject_control_characters,
    require_http_url,
    require_https_url,
    require_identifier,
    require_ws_url,
    safe_join,
    sanitize_header_filename,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

# One representative of each C0 band plus DEL. urlparse/loggers/header writers
# disagree about these, which is exactly why the helpers refuse them up front.
CONTROL_CHARS = ["\x00", "\x01", "\t", "\n", "\r", "\x1f", "\x7f"]


@pytest.mark.parametrize("control", CONTROL_CHARS)
def test_reject_control_characters_refuses_every_control_band(control):
    with pytest.raises(HardeningViolation):
        reject_control_characters(f"pre{control}post", "field")


def test_reject_control_characters_accepts_plain_text():
    assert reject_control_characters("abc123-_./", "field") == "abc123-_./"


@pytest.mark.parametrize("control", CONTROL_CHARS)
def test_url_helpers_refuse_control_characters(control):
    for helper in (require_http_url, require_https_url, require_ws_url):
        with pytest.raises(HardeningViolation):
            helper(f"https://example.com/{control}x", "url")


@pytest.mark.parametrize(
    "value",
    [
        "//example.com/path",  # protocol-relative
        "https://user:pass@example.com/x",  # credentials
        "javascript:alert(1)",
        "data:text/html,x",
        "/relative/path",
    ],
)
def test_url_helpers_refuse_non_absolute_or_credentialed(value):
    with pytest.raises(HardeningViolation):
        require_http_url(value, "url")


def test_ws_helper_only_accepts_ws_schemes():
    assert require_ws_url("wss://example.com/ws") == "wss://example.com/ws"
    with pytest.raises(HardeningViolation):
        require_ws_url("https://example.com/ws")


@pytest.mark.parametrize("control", CONTROL_CHARS)
def test_identifiers_refuse_control_characters(control):
    assert not is_identifier(f"use{control}rs")
    with pytest.raises(HardeningViolation):
        require_identifier(f"use{control}rs")


def test_identifier_rejects_oversized_name_and_trailing_newline():
    # One past the 63-char ceiling, and the "$\n" fullmatch bypass.
    with pytest.raises(HardeningViolation):
        require_identifier("a" * 64)
    with pytest.raises(HardeningViolation):
        require_identifier("users\n")
    assert require_identifier("a" * 63) == "a" * 63


@pytest.mark.parametrize("control", CONTROL_CHARS)
def test_header_filename_scrubs_control_characters(control):
    cleaned = sanitize_header_filename(f"report{control}2026.txt")
    assert not any(c in cleaned for c in CONTROL_CHARS)
    assert "report" in cleaned


def test_header_filename_bounds_oversized_input():
    assert len(sanitize_header_filename("a" * 1000)) == 120


def test_content_disposition_never_emits_control_characters_or_oversized_name():
    value = content_disposition(f"a\r\nX-Injected: 1{'b' * 500}.txt")
    assert not any(c in value for c in CONTROL_CHARS)
    assert value.count('"') == 2  # no bare quote can terminate the header value
    assert len(value) < 200


@pytest.mark.parametrize("control", CONTROL_CHARS)
def test_bounded_int_refuses_string_control_payloads(control):
    # Embedded (not trailing) -- int() tolerates surrounding whitespace but a
    # control character inside a numeric token must never be quieted into a
    # number.
    with pytest.raises(HardeningViolation):
        bounded_int(f"1{control}0", "limit", minimum=0, maximum=100)


@pytest.mark.parametrize(
    "value",
    [2**70, 101, -1, True, 10.9, -0.5, math.inf, math.nan, "abc", []],
)
def test_bounded_int_refuses_out_of_range_and_non_integer(value):
    with pytest.raises(HardeningViolation):
        bounded_int(value, "limit", minimum=0, maximum=100)


@pytest.mark.parametrize("value", [0, -1, SNOWFLAKE_MAX + 1, 2**70, True, 1.5])
def test_parse_id_refuses_oversized_or_invalid_ids(value):
    with pytest.raises(HardeningViolation):
        parse_id(value)


def test_parse_id_accepts_the_upper_bound():
    assert parse_id(str(SNOWFLAKE_MAX)) == SNOWFLAKE_MAX


def test_safe_join_refuses_traversal_and_null_byte(tmp_path):
    with pytest.raises(ValueError):  # HardeningViolation subclasses ValueError
        safe_join(str(tmp_path), "../escape")
    with pytest.raises(ValueError):
        safe_join(str(tmp_path), "a\x00b")
