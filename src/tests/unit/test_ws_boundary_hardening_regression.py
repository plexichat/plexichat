"""Regression tests for WebSocket boundary/decompression hardening gaps.

Covers two real weaknesses that let attacker-controlled input slip past the
gateway's defense-in-depth helpers:

1. ``validate_session_id`` used ``re.match`` with a ``$``-anchored pattern.
   Python's ``$`` also matches *before a trailing newline*, so a session id
   like ``"deadbeef\\n"`` was accepted and flowed into Valkey keys/log lines.
2. The explicit decompression size limits (``max_size`` /
   ``max_decompressed_size``) were used verbatim, so a caller (or config path)
   passing an oversized value silently disabled the zip-bomb ceiling; the
   streaming decompressor also accumulated compressed input in an unbounded
   buffer while waiting for a sync-flush marker.
"""

import json
import zlib

import pytest

from src.api.websocket.boundaries import (
    MAX_DECOMPRESSED_BYTES,
    validate_session_id,
)
from src.api.websocket.compression import (
    CompressionError,
    ZlibCompressor,
    ZlibDecompressor,
    decompress_payload,
)
from src.core.security.hardening import HardeningViolation


# --- 1. session id must be fully hex, with no trailing newline -------------


@pytest.mark.parametrize(
    "value",
    [
        "deadbeef\n",          # ``$`` matched before this trailing newline
        "deadbeef\r\n",        # CR + LF
        "a" * 32 + "\n",       # otherwise-valid length, one trailing newline
        "ab\ncd",              # embedded control character
    ],
)
def test_session_id_rejects_trailing_or_embedded_newline(value):
    with pytest.raises(HardeningViolation):
        validate_session_id(value)


def test_session_id_still_accepts_valid_hex():
    assert validate_session_id("deadbeef") == "deadbeef"
    assert validate_session_id("a" * 32) == "a" * 32


# --- 2a. explicit decompression limits cannot disable the bomb ceiling ----


def _bomb_payload():
    """A valid JSON object whose decompressed form exceeds the ceiling."""
    raw = json.dumps({"d": "A" * (MAX_DECOMPRESSED_BYTES + 1)}).encode("utf-8")
    return zlib.compress(raw)


def test_decompress_payload_clamps_explicit_oversized_limit():
    with pytest.raises(CompressionError):
        decompress_payload(_bomb_payload(), max_size=10**9)


def test_streaming_decompressor_clamps_explicit_oversized_limit():
    stream = ZlibCompressor().compress(
        {"d": "A" * (MAX_DECOMPRESSED_BYTES + 1)}
    )
    with pytest.raises(CompressionError):
        ZlibDecompressor(max_decompressed_size=10**9).decompress(stream)


# --- 2b. streaming buffer cannot grow without bound -----------------------


def test_streaming_decompressor_bounds_pending_buffer():
    decompressor = ZlibDecompressor()
    # Each chunk is individually small (would pass the raw-frame ceiling), but
    # without a cumulative bound the pending buffer grows until the socket is
    # closed. Repeated chunks that never carry the sync-flush marker must be
    # refused once the buffer ceiling is crossed.
    with pytest.raises(CompressionError):
        for _ in range(16):
            decompressor.decompress(b"x" * 60000)


def test_streaming_decompressor_still_handles_normal_message():
    stream = ZlibCompressor().compress({"op": 1, "d": {"ok": True}})
    assert ZlibDecompressor().decompress(stream) == {"op": 1, "d": {"ok": True}}
