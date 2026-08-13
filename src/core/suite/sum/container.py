"""PlexiSum container format: compressed SQLite payload with integrity.

File layout::

    Header (16 bytes):
        magic:           b"PXSM"        (4 bytes)
        format_version:  u8 = 1         (1 byte)
        compression:     u8 (0/1)       (1 byte)
        flags:           u8             (1 byte)
        reserved:        zeroed         (9 bytes)

    Body:  zstd-compressed (or raw) payload
        If WAL flag set:  sqlite_db_bytes + wal_bytes
        Otherwise:        sqlite_db_bytes

    Footer (32 bytes):
        SHA-256 checksum of the *uncompressed* payload
"""

import hashlib
import struct

import zstandard as _zstd

# ── format constants ────────────────────────────────────────────────────────

MAGIC = b"PXSM"
HEADER_SIZE = 16
CHECKSUM_SIZE = 32  # SHA-256
CONTAINER_VERSION = 1

COMPRESS_NONE = 0
COMPRESS_ZSTD = 1

FLAG_WAL = 1 << 0
FLAG_ENCRYPTED = 1 << 1


# ── compression helpers ─────────────────────────────────────────────────────


def compress_zstd(data: bytes) -> bytes:
    """Compress *data* with zstandard (default level)."""
    return _zstd.ZstdCompressor().compress(data)


def decompress_zstd(data: bytes) -> bytes:
    """Decompress zstandard-compressed *data*."""
    return _zstd.ZstdDecompressor().decompress(data)


# ── encode / decode ─────────────────────────────────────────────────────────


def encode_container(
    db_bytes: bytes,
    wal_bytes: bytes | None = None,
    compression: int = COMPRESS_ZSTD,
) -> bytes:
    """Wrap raw SQLite bytes into the PlexiSum container format.

    *compression* is ``COMPRESS_ZSTD`` (default) or ``COMPRESS_NONE``.
    The default always zstd-compresses the payload; ``COMPRESS_NONE`` is kept
    only for explicit uncompressed encodes (e.g. in-browser saves) and for
    decoding legacy files — there is no silent fallback.
    """
    if wal_bytes:
        # Store db_len (4 bytes big-endian) + db_bytes + wal_bytes
        payload = len(db_bytes).to_bytes(4, "big") + db_bytes + wal_bytes
    else:
        payload = db_bytes
    checksum = hashlib.sha256(payload).digest()

    flags = FLAG_WAL if wal_bytes else 0

    if compression == COMPRESS_ZSTD:
        compressed = compress_zstd(payload)
        compression_byte = COMPRESS_ZSTD
    elif compression == COMPRESS_NONE:
        compressed = payload
        compression_byte = COMPRESS_NONE
    else:
        raise ValueError(f"Unknown compression mode: {compression}")

    header = (
        MAGIC
        + struct.pack("BBB", CONTAINER_VERSION, compression_byte, flags)
        + b"\x00" * 9
    )

    return header + compressed + checksum


def decode_container(data: bytes) -> tuple[bytes, bytes | None, dict]:
    """Decode a PlexiSum container.

    Returns ``(db_bytes, wal_bytes, metadata)``.
    *wal_bytes* is ``None`` when the WAL flag is not set (the common case).
    Raises ``ValueError`` on bad magic, unknown version, or checksum mismatch.
    """
    if len(data) < HEADER_SIZE + CHECKSUM_SIZE:
        raise ValueError("Data too short to be a valid PlexiSum container")

    if data[:4] != MAGIC:
        raise ValueError(f"Invalid magic: expected {MAGIC!r}, got {data[:4]!r}")

    version = data[4]
    if version != CONTAINER_VERSION:
        raise ValueError(f"Unsupported container version: {version}")

    compression = data[5]
    flags = data[6]

    stored_checksum = data[-CHECKSUM_SIZE:]
    compressed_body = data[HEADER_SIZE:-CHECKSUM_SIZE]

    if compression == COMPRESS_ZSTD:
        payload = decompress_zstd(compressed_body)
    elif compression == COMPRESS_NONE:
        payload = compressed_body
    else:
        raise ValueError(f"Unknown compression mode: {compression}")

    actual_checksum = hashlib.sha256(payload).digest()
    if actual_checksum != stored_checksum:
        raise ValueError("Container checksum mismatch (data corrupted)")

    has_wal = bool(flags & FLAG_WAL)
    if has_wal:
        if len(payload) < 4:
            raise ValueError("WAL payload too short")
        db_len = int.from_bytes(payload[:4], "big")
        db_bytes = payload[4 : 4 + db_len]
        wal_bytes = payload[4 + db_len :]
    else:
        db_bytes = payload
        wal_bytes = None

    metadata: dict = {
        "version": version,
        "compression": compression,
        "flags": flags,
        "has_wal": has_wal,
        "uncompressed_size": len(payload),
        "compressed_size": len(compressed_body),
    }

    return db_bytes, wal_bytes, metadata
