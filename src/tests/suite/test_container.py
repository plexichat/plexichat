"""PlexiSum container format: encode/decode round-trips and integrity."""

import pytest

from src.core.suite.sum.container import (
    COMPRESS_NONE,
    COMPRESS_ZSTD,
    FLAG_WAL,
    decode_container,
    encode_container,
)


def test_default_encode_always_zstd_compresses():
    payload = b"sqlite-database-bytes" * 100
    container = encode_container(payload)
    # Default compression byte must be zstd (1), never raw (0) — no silent
    # fallback to uncompressed output.
    assert container[5] == COMPRESS_ZSTD
    assert len(container) < len(payload)


def test_zstd_round_trip():
    payload = b"\x00\x01\x02" * 1000
    container = encode_container(payload, compression=COMPRESS_ZSTD)
    db_bytes, wal_bytes, meta = decode_container(container)
    assert db_bytes == payload
    assert wal_bytes is None
    assert meta["has_wal"] is False
    assert meta["compression"] == COMPRESS_ZSTD


def test_none_round_trip_is_supported_for_legacy_and_browser_saves():
    payload = b"uncompressed-payload"
    container = encode_container(payload, compression=COMPRESS_NONE)
    assert container[5] == COMPRESS_NONE
    db_bytes, wal_bytes, _ = decode_container(container)
    assert db_bytes == payload
    assert wal_bytes is None


def test_wal_flag_round_trip():
    db = b"db-bytes"
    wal = b"wal-bytes"
    container = encode_container(db, wal_bytes=wal, compression=COMPRESS_ZSTD)
    assert container[6] & FLAG_WAL
    decoded_db, decoded_wal, meta = decode_container(container)
    assert decoded_db == db
    assert decoded_wal == wal
    assert meta["has_wal"] is True


def test_unknown_compression_mode_rejected_on_encode():
    with pytest.raises(ValueError):
        encode_container(b"x", compression=99)


def test_decode_rejects_bad_magic():
    with pytest.raises(ValueError):
        decode_container(b"NOTP" + b"\x00" * 60)


def test_decode_rejects_truncated_data():
    with pytest.raises(ValueError):
        decode_container(b"PXSM\x01\x01\x00" + b"\x00" * 9)


def test_decode_rejects_checksum_mismatch():
    container = bytearray(encode_container(b"original-payload"))
    # Corrupt the final checksum byte.
    container[-1] ^= 0xFF
    with pytest.raises(ValueError):
        decode_container(bytes(container))
