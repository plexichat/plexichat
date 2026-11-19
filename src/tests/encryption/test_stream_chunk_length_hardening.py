"""Regression tests for bounding attacker-controlled streaming chunk lengths.

A ``PXSTR`` blob stores each chunk's ciphertext length in a 4-byte,
unauthenticated header field. The decryption loop used to feed that value
straight into ``read_exactly()``/``stream.read()``. For a file-backed stream a
corrupt blob could therefore make the reader attempt a multi-gigabyte
allocation (or block on an unbounded network read), turning a small piece of
at-rest metadata into a memory-exhaustion / resource DoS before any AES-GCM
tag check ever ran.

New writes are not affected: a chunk's ciphertext is at most one plaintext
chunk plus the 16-byte GCM tag, well under the cap enforced here.
"""

from __future__ import annotations

import io
import os
import struct

import pytest

from src.utils.encryption.file_encryption import (
    MAX_CHUNK_CIPHERTEXT_BYTES,
    NONCE_SIZE,
    STREAM_MAGIC,
    STREAM_VERSION,
    DecryptionError,
    Keyring,
    StreamingFileEncryptor,
)


class _SpyStream(io.BytesIO):
    """BytesIO that records the largest ``read(n)`` it was asked for."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_read = 0

    def read(self, n=-1):
        if n is not None and n > 0:
            self.max_read = max(self.max_read, n)
        return super().read(n)


@pytest.fixture
def encryptor(tmp_path):
    return StreamingFileEncryptor(Keyring(tmp_path / "keyring.json"), chunk_size=64)


def _make_blob(encryptor, size):
    data = os.urandom(size)
    out = io.BytesIO()
    encryptor.encrypt_stream(io.BytesIO(data), out, size)
    return out.getvalue(), data


def _first_chunk_len_offset(blob):
    assert blob[:5] == STREAM_MAGIC
    assert blob[5] == STREAM_VERSION
    wrapped_key_len = struct.unpack(">H", blob[10:12])[0]
    # magic(5) + version(1) + key_version(4) + wrapped_key_len(2)
    # + wrapped_key + original_size(8) + chunk_size(4) + nonce(12)
    return 5 + 1 + 4 + 2 + wrapped_key_len + 8 + 4 + NONCE_SIZE


class TestStreamChunkLengthBounds:
    def test_oversized_declared_chunk_length_is_rejected_before_huge_read(
        self, encryptor
    ):
        blob, _ = _make_blob(encryptor, 64 * 5)
        blob = bytearray(blob)
        struct.pack_into(">I", blob, _first_chunk_len_offset(blob), 0xFFFFFFFF)

        spy = _SpyStream(bytes(blob))
        with pytest.raises(DecryptionError, match="chunk length"):
            list(encryptor.decrypt_stream_generator(spy))

        # The bad length must be rejected up front: the reader must never be
        # asked to allocate/read the attacker-declared 4 GiB.
        assert spy.max_read < MAX_CHUNK_CIPHERTEXT_BYTES

    def test_small_declared_chunk_length_is_rejected(self, encryptor):
        """A chunk shorter than the 16-byte GCM tag cannot be authentic."""
        blob, _ = _make_blob(encryptor, 64 * 3)
        blob = bytearray(blob)
        struct.pack_into(">I", blob, _first_chunk_len_offset(blob), 1)

        with pytest.raises(DecryptionError, match="chunk length"):
            list(encryptor.decrypt_stream_generator(io.BytesIO(bytes(blob))))

    def test_legitimate_roundtrip_still_works(self, encryptor):
        blob, data = _make_blob(encryptor, 64 * 5)
        out = io.BytesIO()
        result = encryptor.decrypt_stream(io.BytesIO(blob), out)
        assert out.getvalue() == data
        assert result["verified"] is True

    def test_megabyte_chunk_size_still_roundtrips(self, tmp_path):
        """A chunk size in the megabyte range is below the cap (no regression)."""
        large = StreamingFileEncryptor(
            Keyring(tmp_path / "keyring.json"), chunk_size=1024 * 1024
        )
        blob, data = _make_blob(large, (1024 * 1024) + 17)
        out = io.BytesIO()
        large.decrypt_stream(io.BytesIO(blob), out)
        assert out.getvalue() == data
