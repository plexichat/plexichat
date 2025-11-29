"""
Gateway compression - zlib-stream compression support.
"""

import zlib
from typing import Optional, Tuple
import json


ZLIB_SUFFIX = b"\x00\x00\xff\xff"


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
    """Handles zlib-stream decompression for gateway messages."""

    def __init__(self):
        """Initialize the decompressor."""
        self._decompressor = zlib.decompressobj()
        self._buffer = bytearray()

    def decompress(self, data: bytes) -> Optional[dict]:
        """
        Decompress zlib-stream data to dictionary.

        Args:
            data: Compressed bytes

        Returns:
            Decompressed dictionary or None if incomplete
        """
        self._buffer.extend(data)

        if len(self._buffer) < 4 or self._buffer[-4:] != ZLIB_SUFFIX:
            return None

        try:
            decompressed = self._decompressor.decompress(bytes(self._buffer))
            self._buffer.clear()
            return json.loads(decompressed.decode("utf-8"))
        except (zlib.error, json.JSONDecodeError):
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


def decompress_payload(data: bytes) -> Optional[dict]:
    """
    Decompress a single payload (non-streaming).

    Args:
        data: Compressed bytes

    Returns:
        Decompressed dictionary or None on error
    """
    try:
        decompressed = zlib.decompress(data)
        return json.loads(decompressed.decode("utf-8"))
    except (zlib.error, json.JSONDecodeError):
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
