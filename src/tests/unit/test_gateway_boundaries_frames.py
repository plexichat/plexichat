"""Frame-size and decoded-shape boundaries for the WebSocket gateway."""

import json

import pytest

from src.api.websocket.boundaries import (
    MAX_FRAME_BYTES,
    MAX_PARSED_DEPTH,
    decode_gateway_frame,
    validate_frame_bytes,
    validate_parsed_structure,
)
from src.core.security.hardening import HardeningViolation


def test_frame_bytes_ceiling():
    assert validate_frame_bytes(b"x" * 16) is True
    assert validate_frame_bytes(b"x" * (MAX_FRAME_BYTES + 1)) is False


def test_decode_valid_object():
    assert decode_gateway_frame(b'{"op": 1, "d": null}') == {"op": 1, "d": None}


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[1, 2, 3]",
        b"42",
        b'"a string"',
        b"\xff\xfe",
    ],
)
def test_decode_rejects_non_objects_and_bad_encoding(raw):
    with pytest.raises(HardeningViolation):
        decode_gateway_frame(raw)


def test_decode_rejects_oversized_frame():
    raw = json.dumps({"d": "x" * (MAX_FRAME_BYTES + 1)}).encode()
    with pytest.raises(HardeningViolation):
        decode_gateway_frame(raw)


def test_decode_rejects_deep_structure():
    nested = 0
    for _ in range(MAX_PARSED_DEPTH + 3):
        nested = {"a": nested}
    with pytest.raises(HardeningViolation):
        validate_parsed_structure(nested)


def test_decode_rejects_oversized_string_value():
    with pytest.raises(HardeningViolation):
        validate_parsed_structure({"d": "x" * (65 * 1024)})


def test_configured_limit_is_clamped_to_ceiling(monkeypatch):
    from src.api.websocket import compression

    monkeypatch.setattr(
        compression.config,
        "get",
        lambda key, default=None: (
            {
                "max_message_size": 10**9,
                "max_decompressed_size": 10**9,
            }
            if key == "websocket"
            else default
        ),
    )
    max_msg, max_decomp = compression._get_limits()
    assert max_msg == compression.DEFAULT_MAX_MESSAGE_SIZE
    assert max_msg <= MAX_FRAME_BYTES
    assert max_decomp == compression.DEFAULT_MAX_DECOMPRESSED_SIZE
