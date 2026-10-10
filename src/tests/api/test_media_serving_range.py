"""Regression tests for the attachment Range parser (RFC 9110 suffix ranges).

``serve_attachment`` used to split ``bytes=-500`` into ``("", "500")`` and,
because the empty start was falsy, serve the FIRST 501 bytes behind a
``Content-Range: bytes 0-500/<size>`` header -- the opposite of the requested
final 500 bytes, which media clients use to fetch the tail (e.g. the moov
atom) of a file. These assertions pin the semantics to the shared reference
parser in ``src.static_client.router._parse_range``.
"""

from __future__ import annotations

from src.api.media_serving import _parse_byte_range


def test_suffix_range_serves_the_tail():
    # ``bytes=-500`` means the LAST 500 bytes, not the first 500.
    assert _parse_byte_range("bytes=-500", 10000) == (9500, 9999, True)


def test_suffix_longer_than_file_yields_whole_body():
    assert _parse_byte_range("bytes=-500", 400) == (0, 399, True)


def test_explicit_range():
    assert _parse_byte_range("bytes=0-499", 10000) == (0, 499, True)
    assert _parse_byte_range("bytes=500-999", 10000) == (500, 999, True)


def test_open_ended_range():
    assert _parse_byte_range("bytes=500-", 10000) == (500, 9999, True)


def test_end_clamped_to_file():
    assert _parse_byte_range("bytes=0-99999", 10000) == (0, 9999, True)


def test_no_or_foreign_header_serves_full_body():
    assert _parse_byte_range(None, 100) == (0, 99, False)
    assert _parse_byte_range("", 100) == (0, 99, False)
    assert _parse_byte_range("items=0-5", 100) == (0, 99, False)


def test_multi_range_falls_back_to_full_body():
    assert _parse_byte_range("bytes=0-1,5-9", 1000) == (0, 999, False)


def test_unsatisfiable_range_falls_back_to_full_body():
    # Start at/after EOF and inverted ranges fall back to a 200 full body.
    assert _parse_byte_range("bytes=1000-", 1000) == (0, 999, False)
    assert _parse_byte_range("bytes=5-3", 1000) == (0, 999, False)


def test_malformed_range_falls_back_to_full_body():
    assert _parse_byte_range("bytes=abc", 1000) == (0, 999, False)
    assert _parse_byte_range("bytes=-0", 1000) == (0, 999, False)


def test_empty_file_stays_full_body():
    assert _parse_byte_range(None, 0) == (0, -1, False)
    assert _parse_byte_range("bytes=-100", 0) == (0, -1, False)
