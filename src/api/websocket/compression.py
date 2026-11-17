"""
Gateway compression - zlib-stream compression support with security protections.

Security features:
- Maximum decompressed size limit to prevent zip bombs
- Maximum compressed input size limit
- Configurable limits via config
"""

import zlib
from typing import Any, Optional, Tuple
import json

import utils.config as config
import utils.logger as logger

from .boundaries import (
    MAX_DECOMPRESSED_BYTES,
    MAX_FRAME_BYTES,
    clamp_configured_limit,
)


ZLIB_SUFFIX = b"\x00\x00\xff\xff"

# Default security limits (can be overridden via config)
DEFAULT_MAX_MESSAGE_SIZE = 65536  # 64KB max compressed message
DEFAULT_MAX_DECOMPRESSED_SIZE = 262144  # 256KB max decompressed (4:1 ratio protection)


def _get_limits() -> Tuple[int, int]:
    """Get compression limits from config (with fallback for tests).

    Config values are clamped to absolute ceilings so a single bad config
    entry cannot disable the frame-size guard. An invalid value is replaced by
    the built-in default rather than propagating into the receive loop (which
    would tear down every connection).
    """
    try:
        ws_config = config.get("websocket", {})
        max_msg = ws_config.get("max_message_size", DEFAULT_MAX_MESSAGE_SIZE)
        max_decomp = ws_config.get(
            "max_decompressed_size", DEFAULT_MAX_DECOMPRESSED_SIZE
        )
    except RuntimeError:
        # Config not set up (e.g., in tests) - use defaults
        return DEFAULT_MAX_MESSAGE_SIZE, DEFAULT_MAX_DECOMPRESSED_SIZE

    def _clamp(value: Any, field: str, ceiling: int, default: int) -> int:
        try:
            return clamp_configured_limit(value, field, ceiling, default)
        except ValueError:
            logger.warning("Invalid websocket.%s; using default", field)
            return default

    return (
        _clamp(max_msg, "max_message_size", MAX_FRAME_BYTES, DEFAULT_MAX_MESSAGE_SIZE),
        _clamp(
            max_decomp,
            "max_decompressed_size",
            MAX_DECOMPRESSED_BYTES,
            DEFAULT_MAX_DECOMPRESSED_SIZE,
        ),
    )


def _clamp_decompression_limit(value: Any, default: int) -> int:
    """Clamp an explicit decompression ceiling into ``[1, MAX_DECOMPRESSED_BYTES]``.

    The public helpers accept an explicit ``max_size``/``max_decompressed_size``
    argument. Used verbatim, an oversized value silently disabled the zip-bomb
    guard even though the config path was clamped. Anything outside the ceiling
    is replaced by the built-in default instead of being trusted, so a caller
    can never widen the protection beyond the absolute bound.
    """
    try:
        return clamp_configured_limit(
            value, "decompression size limit", MAX_DECOMPRESSED_BYTES, default
        )
    except (ValueError, TypeError):
        return default


class CompressionError(Exception):
    """Raised when compression/decompression fails due to security limits."""

    pass


class ZlibCompressor:
    """Handles zlib-stream compression for gateway messages."""

    def __init__(self):
        """Initialize the compressor."""
        self._compressor = zlib.compressobj()

    def compress(self, data: dict) -> bytes:
        """
        Compress a dictionary to zlib-stream format.

        Args:
            data: Dictionary to compress

        Returns:
            Compressed bytes
        """
        json_bytes = json.dumps(data).encode("utf-8")
        compressed = self._compressor.compress(json_bytes)
        compressed += self._compressor.flush(zlib.Z_SYNC_FLUSH)
        return compressed

    def reset(self) -> None:
        """Reset the compressor state."""
        self._compressor = zlib.compressobj()


class ZlibDecompressor:
    """Handles zlib-stream decompression for gateway messages with security limits."""

    def __init__(self, max_decompressed_size: Optional[int] = None):
        """
        Initialize the decompressor.

        Args:
            max_decompressed_size: Maximum allowed decompressed size (prevents zip bombs)
        """
        self._decompressor = zlib.decompressobj()
        self._buffer = bytearray()
        max_msg, default_max = _get_limits()
        # Never trust an explicit ceiling past the absolute bound: a caller
        # passing a huge value would otherwise turn off zip-bomb protection.
        self._max_size = (
            default_max
            if max_decompressed_size is None
            else _clamp_decompression_limit(max_decompressed_size, default_max)
        )
        # A streaming message's compressed form must fit in the same bounded
        # frame any other inbound payload does. Without this, a peer that keeps
        # sending fragments without the sync-flush marker grows ``_buffer``
        # without limit, which is a memory-exhaustion vector of its own.
        self._max_buffer = max_msg

    def decompress(self, data: bytes) -> Optional[dict]:
        """
        Decompress zlib-stream data to dictionary with size limits.

        Args:
            data: Compressed bytes

        Returns:
            Decompressed dictionary or None if incomplete

        Raises:
            CompressionError: If decompressed size exceeds limit (zip bomb protection)
                or if the pending compressed buffer exceeds its ceiling.
        """
        if not isinstance(data, (bytes, bytearray)):
            self.reset()
            raise CompressionError("decompression input must be bytes")
        # Bound the pending input *before* buffering it. Each fragment may be
        # individually small, so checking a single chunk is not enough; the
        # cumulative size is what an attacker can grow without a sync flush.
        if len(data) > self._max_buffer or (
            len(self._buffer) + len(data) > self._max_buffer
        ):
            self.reset()
            logger.warning(
                "Decompression pending buffer exceeded %s bytes - refusing stream",
                self._max_buffer,
            )
            raise CompressionError(
                f"Compressed input exceeds maximum buffered size of "
                f"{self._max_buffer} bytes"
            )
        self._buffer.extend(data)

        if len(self._buffer) < 4 or self._buffer[-4:] != ZLIB_SUFFIX:
            return None

        try:
            # Use max_length parameter to limit decompression (zip bomb protection)
            decompressed = self._decompressor.decompress(
                bytes(self._buffer), max_length=self._max_size
            )

            # Check if there's more data (indicates we hit the limit)
            if self._decompressor.unconsumed_tail:
                self._buffer.clear()
                self.reset()
                logger.warning(
                    f"Decompression exceeded max size limit ({self._max_size} bytes) - possible zip bomb"
                )
                raise CompressionError(
                    f"Decompressed data exceeds maximum size of {self._max_size} bytes"
                )

            self._buffer.clear()
            return json.loads(decompressed.decode("utf-8"))
        except zlib.error as e:
            logger.warning(f"Zlib decompression error: {e}")
            self._buffer.clear()
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"JSON decode error after decompression: {e}")
            self._buffer.clear()
            return None

    def reset(self) -> None:
        """Reset the decompressor state."""
        self._decompressor = zlib.decompressobj()
        self._buffer.clear()


def compress_payload(data: dict) -> bytes:
    """
    Compress a single payload (non-streaming).

    Args:
        data: Dictionary to compress

    Returns:
        Compressed bytes
    """
    json_bytes = json.dumps(data).encode("utf-8")
    return zlib.compress(json_bytes)


def decompress_payload(data: bytes, max_size: Optional[int] = None) -> Optional[dict]:
    """
    Decompress a single payload (non-streaming) with size limit.

    Args:
        data: Compressed bytes
        max_size: Maximum decompressed size (defaults to config value)

    Returns:
        Decompressed dictionary or None on error

    Raises:
        CompressionError: If decompressed size exceeds limit
    """
    _, default_max = _get_limits()
    # An explicit limit is a request, not a way to opt out of the absolute
    # ceiling. Clamp it like the config path so the zip-bomb guard cannot be
    # disabled by a caller passing an oversized value.
    limit = (
        default_max
        if max_size is None
        else _clamp_decompression_limit(max_size, default_max)
    )

    try:
        # Create decompressor with size limit
        decompressor = zlib.decompressobj()
        decompressed = decompressor.decompress(data, max_length=limit)

        # Check for unconsumed data (hit the limit)
        if decompressor.unconsumed_tail:
            logger.warning(f"Payload decompression exceeded max size ({limit} bytes)")
            raise CompressionError(
                f"Decompressed data exceeds maximum size of {limit} bytes"
            )

        return json.loads(decompressed.decode("utf-8"))
    except zlib.error as e:
        logger.debug(f"Zlib error: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.debug(f"JSON decode error: {e}")
        return None


def is_compressed(data: bytes) -> bool:
    """
    Check if data appears to be zlib compressed.

    Args:
        data: Bytes to check

    Returns:
        True if likely compressed
    """
    if len(data) < 2:
        return False
    return data[0] == 0x78 and data[1] in (0x01, 0x5E, 0x9C, 0xDA)


def validate_message_size(data: bytes) -> bool:
    """
    Validate that compressed message size is within limits.

    Args:
        data: Compressed message bytes

    Returns:
        True if size is acceptable
    """
    max_msg, _ = _get_limits()
    # ``data`` is the raw frame bytes; this runs before json.loads.
    return 0 <= len(data) <= max_msg
