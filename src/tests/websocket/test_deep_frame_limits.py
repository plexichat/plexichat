"""Deep hardening: oversized frames are rejected pre-parse with a clear code."""

import json

import pytest

import src.api.websocket.compression as compression
from src.api.websocket.boundaries import MAX_FRAME_BYTES
from src.api.websocket.frames import (
    FrameLimitExceeded,
    decode_inbound_frame,
    enforce_frame_size,
    inbound_frame_limit,
)
from src.api.websocket.opcodes import (
    GatewayCloseCode,
    get_close_message,
    is_resumable,
)


def test_message_too_big_is_a_distinct_clear_close_code():
    assert int(GatewayCloseCode.MESSAGE_TOO_BIG) != int(GatewayCloseCode.DECODE_ERROR)
    message = get_close_message(int(GatewayCloseCode.MESSAGE_TOO_BIG))
    assert "size" in message.lower()
    assert is_resumable(int(GatewayCloseCode.MESSAGE_TOO_BIG))


def test_enforce_accepts_bounded_frame():
    raw = b'{"op": 1, "d": null}'
    assert enforce_frame_size(raw) == raw


def test_oversized_frame_is_rejected_before_json_parsing():
    # Invalid JSON that is also far over the ceiling: the size guard must win
    # before a JSON/UTF-8 decoder ever sees the payload.
    raw = b"\xff" * (MAX_FRAME_BYTES + 1)
    with pytest.raises(FrameLimitExceeded) as exc:
        decode_inbound_frame(raw)
    assert exc.value.close_code == int(GatewayCloseCode.MESSAGE_TOO_BIG)


def test_oversized_valid_json_still_raises_size_error():
    raw = json.dumps({"op": 1, "d": "x" * (MAX_FRAME_BYTES + 1)}).encode()
    with pytest.raises(FrameLimitExceeded) as exc:
        enforce_frame_size(raw)
    assert exc.value.limit <= MAX_FRAME_BYTES
    assert exc.value.close_code == int(GatewayCloseCode.MESSAGE_TOO_BIG)


def test_configured_limit_is_honoured_but_clamped(monkeypatch):
    monkeypatch.setattr(
        compression, "_get_limits", lambda: (1024, 4096)
    )
    assert inbound_frame_limit() == 1024
    with pytest.raises(FrameLimitExceeded) as exc:
        enforce_frame_size(b"x" * 1025)
    assert exc.value.limit == 1024

    monkeypatch.setattr(
        compression, "_get_limits", lambda: (MAX_FRAME_BYTES * 4, MAX_FRAME_BYTES * 4)
    )
    assert inbound_frame_limit() == MAX_FRAME_BYTES


def test_decode_inbound_frame_decodes_bounded_object():
    raw = b'{"op": 1, "d": {"x": 1}}'
    assert decode_inbound_frame(raw) == {"op": 1, "d": {"x": 1}}
