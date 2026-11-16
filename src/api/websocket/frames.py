"""Pre-parse inbound frame guard with an explicit rejection close code.

The receive loop used to report every oversized frame as ``DECODE_ERROR``
(4002), which is indistinguishable from malformed JSON and gives clients no
hint that they should shrink a payload. This module centralises the byte-ceiling
check so it can run *before* decompression or ``json.loads`` and carry a
dedicated :class:`~src.api.websocket.opcodes.GatewayCloseCode.MESSAGE_TOO_BIG`
close code through a typed exception.
"""

from .boundaries import MAX_FRAME_BYTES, validate_frame_bytes
from .opcodes import GatewayCloseCode


class FrameLimitExceeded(Exception):
    """An inbound frame exceeded the byte ceiling before any parsing."""

    def __init__(self, message: str, limit: int) -> None:
        super().__init__(message)
        self.limit = int(limit)

    @property
    def close_code(self) -> int:
        """The gateway close code clients should receive for this rejection."""
        return int(GatewayCloseCode.MESSAGE_TOO_BIG)


def inbound_frame_limit() -> int:
    """Effective raw-frame ceiling: configured value clamped to the hard cap.

    An unreadable or oversized configuration never disables the guard; an
    operator may lower the ceiling but not raise it past ``MAX_FRAME_BYTES``.
    """
    try:
        from .compression import _get_limits

        configured, _ = _get_limits()
        return max(1, min(int(configured), MAX_FRAME_BYTES))
    except Exception:
        return MAX_FRAME_BYTES


def enforce_frame_size(raw: object) -> bytes:
    """Return ``raw`` as bytes or raise :class:`FrameLimitExceeded`.

    This is deliberately called on the raw ``text``/``bytes`` payload *before*
    any zlib decompression or JSON decoding, so a hostile frame is rejected
    without paying for either.
    """
    if not isinstance(raw, (bytes, bytearray)) or not validate_frame_bytes(bytes(raw)):
        raise FrameLimitExceeded(
            "gateway frame is not valid bounded bytes", MAX_FRAME_BYTES
        )
    limit = inbound_frame_limit()
    if len(raw) > limit:
        raise FrameLimitExceeded(f"gateway frame exceeds {limit} bytes", limit)
    return bytes(raw)


def decode_inbound_frame(raw: object) -> dict:
    """Enforce the byte ceiling, then decode one bounded JSON object."""
    from .boundaries import decode_gateway_frame

    return decode_gateway_frame(enforce_frame_size(raw))
