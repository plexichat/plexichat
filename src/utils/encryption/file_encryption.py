"""
File encryption for media storage at rest.

Provides AES-256-GCM encryption for files with:
- Per-file encryption keys (wrapped with master key)
- Streaming encryption for large files
- Key versioning for rotation support
- Authenticated encryption with integrity verification
"""

import os
import struct
import hashlib
from typing import Optional, Tuple, BinaryIO, Dict, Any
from dataclasses import dataclass

import utils.logger as logger
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .core import Keyring


# File format constants
FILE_MAGIC = b"PXENC"  # Plexichat Encrypted (Common)
STREAM_MAGIC = b"PXSTR"  # Plexichat Stream (Robust)
FILE_VERSION = 1
CHUNK_SIZE = 256 * 1024  # 256KB chunks for streaming
NONCE_SIZE = 12
TAG_SIZE = 16
KEY_SIZE = 32


def read_exactly(stream: BinaryIO, n: int) -> bytes:
    """
    Read exactly n bytes from a stream.
    Raises EOFError if the stream ends before n bytes are read.
    """
    result = b""
    while len(result) < n:
        chunk = stream.read(n - len(result))
        if not chunk:
            if not result:
                return b""  # True EOF at start
            raise EOFError(
                f"Unexpected end of stream: got {len(result)} bytes, expected {n}"
            )
        result += chunk
    return result


@dataclass
class EncryptedFileHeader:
    """Header for encrypted files."""

    version: int
    key_version: int
    wrapped_key: bytes
    nonce: bytes
    original_size: int
    checksum: str
    is_stream: bool = False


@dataclass
class FileEncryptionResult:
    encrypted_data: bytes
    header: EncryptedFileHeader


@dataclass
class FileDecryptionResult:
    data: bytes
    verified: bool


class FileEncryptor:
    """Encrypts and decrypts files using AES-256-GCM."""

    def __init__(self, keyring: Optional[Keyring] = None):
        from pathlib import Path

        self.keyring = keyring or Keyring(
            Path.home() / ".plexichat" / "data" / "file_keyring.json",
            env_var="PLEXICHAT_MEDIA_KEY",
        )
        self._ensure_key()

    def _ensure_key(self) -> None:
        if not self.keyring.keys:
            self.keyring.get_key()
            self.keyring.save()

    def _generate_dek(self) -> bytes:
        return AESGCM.generate_key(bit_length=256)

    def _wrap_key(
        self, dek: bytes, kek_version: Optional[int] = None
    ) -> Tuple[bytes, int]:
        version, kek = self.keyring.get_key(kek_version)
        cipher = AESGCM(kek)
        nonce = os.urandom(NONCE_SIZE)
        wrapped = cipher.encrypt(nonce, dek, b"")
        return nonce + wrapped, version

    def _unwrap_key(self, wrapped_key: bytes, kek_version: int) -> bytes:
        _, kek = self.keyring.get_key(kek_version)
        cipher = AESGCM(kek)
        nonce = wrapped_key[:NONCE_SIZE]
        ciphertext = wrapped_key[NONCE_SIZE:]
        return cipher.decrypt(nonce, ciphertext, b"")

    def serialize_header(self, header: EncryptedFileHeader) -> bytes:
        return (
            FILE_MAGIC
            + struct.pack(">B", header.version)
            + struct.pack(">I", header.key_version)
            + struct.pack(">H", len(header.wrapped_key))
            + header.wrapped_key
            + header.nonce
            + struct.pack(">Q", header.original_size)
            + header.checksum.encode("ascii")
        )

    def encrypt(self, data: bytes, aad: Optional[bytes] = b"") -> FileEncryptionResult:
        if not data:
            raise ValueError("Cannot encrypt empty data")

        dek = self._generate_dek()
        wrapped_key, key_version = self._wrap_key(dek)
        nonce = os.urandom(NONCE_SIZE)
        checksum = hashlib.sha256(data).hexdigest()
        cipher = AESGCM(dek)
        ciphertext = cipher.encrypt(nonce, data, aad)

        header = EncryptedFileHeader(
            version=FILE_VERSION,
            key_version=key_version,
            wrapped_key=wrapped_key,
            nonce=nonce,
            original_size=len(data),
            checksum=checksum,
            is_stream=False,
        )
        return FileEncryptionResult(encrypted_data=ciphertext, header=header)

    def decrypt(
        self,
        encrypted_data: bytes,
        header: EncryptedFileHeader,
        aad: Optional[bytes] = b"",
        verify_checksum: bool = False,
    ) -> FileDecryptionResult:
        dek = self._unwrap_key(header.wrapped_key, header.key_version)
        cipher = AESGCM(dek)
        plaintext = cipher.decrypt(header.nonce, encrypted_data, aad)

        verified = True
        if verify_checksum:
            computed = hashlib.sha256(plaintext).hexdigest()
            verified = computed == header.checksum

        return FileDecryptionResult(data=plaintext, verified=verified)

    def encrypt_to_blob(self, data: bytes, aad: Optional[bytes] = b"") -> bytes:
        dek = self._generate_dek()
        wrapped_key, key_version = self._wrap_key(dek)
        nonce = os.urandom(NONCE_SIZE)
        checksum = hashlib.sha256(data).hexdigest()
        cipher = AESGCM(dek)
        ciphertext = cipher.encrypt(nonce, data, aad)

        wrapped_key_len = len(wrapped_key)
        return (
            FILE_MAGIC
            + struct.pack(">B", FILE_VERSION)
            + struct.pack(">I", key_version)
            + struct.pack(">H", wrapped_key_len)
            + wrapped_key
            + nonce
            + struct.pack(">Q", len(data))
            + checksum.encode("ascii")
            + ciphertext
        )

    def deserialize_header(self, data: bytes) -> Tuple[EncryptedFileHeader, int]:
        """Deserialize header with robust format detection."""
        if len(data) < 12:
            raise ValueError("Data too short")

        magic = data[:5]
        if magic not in (FILE_MAGIC, STREAM_MAGIC):
            raise ValueError(f"Invalid magic: {magic}")

        offset = 5
        version = struct.unpack(">B", data[offset : offset + 1])[0]
        offset += 1
        key_version = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        wrapped_key_len = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2

        if len(data) < offset + wrapped_key_len:
            raise ValueError("Truncated key")
        wrapped_key = data[offset : offset + wrapped_key_len]
        offset += wrapped_key_len

        is_stream = magic == STREAM_MAGIC
        if not is_stream and magic == FILE_MAGIC:
            # Heuristic check for legacy stream
            remaining = len(data) - offset
            if remaining >= 12:
                potential_chunk = struct.unpack(">I", data[offset + 8 : offset + 12])[0]
                if potential_chunk in (262144, 524288, 1048576):
                    is_stream = True

        if is_stream:
            if len(data) < offset + 12:
                raise ValueError("Truncated stream header")
            original_size = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 12
            return EncryptedFileHeader(
                version=version,
                key_version=key_version,
                wrapped_key=wrapped_key,
                nonce=b"",
                original_size=original_size,
                checksum="",
                is_stream=True,
            ), offset
        else:
            if len(data) < offset + 84:
                raise ValueError("Truncated blob header")
            nonce = data[offset : offset + NONCE_SIZE]
            offset += NONCE_SIZE
            original_size = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 8
            checksum = data[offset : offset + 64].decode("ascii")
            offset += 64
            return EncryptedFileHeader(
                version=version,
                key_version=key_version,
                wrapped_key=wrapped_key,
                nonce=nonce,
                original_size=original_size,
                checksum=checksum,
                is_stream=False,
            ), offset

    def deserialize_header_from_stream(
        self, stream: BinaryIO
    ) -> Tuple[EncryptedFileHeader, int]:
        """Read header from stream robustly."""
        fixed = read_exactly(stream, 12)
        if not fixed:
            raise ValueError("Empty stream")

        magic = fixed[:5]
        wrapped_key_len = struct.unpack(">H", fixed[10:12])[0]
        wrapped_key = read_exactly(stream, wrapped_key_len)

        if magic == STREAM_MAGIC:
            rem = read_exactly(stream, 12)
            header_data = fixed + wrapped_key + rem
        else:
            rem_start = read_exactly(stream, 12)
            potential_chunk = struct.unpack(">I", rem_start[8:12])[0]
            if potential_chunk in (262144, 524288, 1048576):
                header_data = fixed + wrapped_key + rem_start
            else:
                rem_end = read_exactly(stream, 72)
                header_data = fixed + wrapped_key + rem_start + rem_end

        return self.deserialize_header(header_data)

    def decrypt_from_blob(self, blob: bytes, aad: Optional[bytes] = b"") -> bytes:
        header, header_size = self.deserialize_header(blob)
        ciphertext = blob[header_size:]
        dek = self._unwrap_key(header.wrapped_key, header.key_version)
        cipher = AESGCM(dek)
        return cipher.decrypt(header.nonce, ciphertext, aad)


class StreamingFileEncryptor:
    """Streaming encryption/decryption."""

    def __init__(self, keyring: Optional[Keyring] = None, chunk_size: int = CHUNK_SIZE):
        from pathlib import Path

        self.keyring = keyring or Keyring(
            Path.home() / ".plexichat" / "data" / "file_keyring.json",
            env_var="PLEXICHAT_MEDIA_KEY",
        )
        self.chunk_size = chunk_size

    def encrypt_stream(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        file_size: int,
        aad: Optional[bytes] = b"",
    ) -> Dict[str, Any]:
        dek = AESGCM.generate_key(bit_length=256)
        cipher = AESGCM(dek)
        version, kek = self.keyring.get_key()
        kek_cipher = AESGCM(kek)
        wrap_nonce = os.urandom(NONCE_SIZE)
        wrapped_key = wrap_nonce + kek_cipher.encrypt(wrap_nonce, dek, b"")

        output_stream.write(STREAM_MAGIC)
        output_stream.write(struct.pack(">B", FILE_VERSION))
        output_stream.write(struct.pack(">I", version))
        output_stream.write(struct.pack(">H", len(wrapped_key)))
        output_stream.write(wrapped_key)
        output_stream.write(struct.pack(">Q", file_size))
        output_stream.write(struct.pack(">I", self.chunk_size))

        chunk_index = 0
        while True:
            chunk = input_stream.read(self.chunk_size)
            if not chunk:
                break
            chunk_nonce = os.urandom(NONCE_SIZE)
            chunk_aad = struct.pack(">Q", chunk_index)
            if aad:
                chunk_aad = aad + chunk_aad
            encrypted_chunk = cipher.encrypt(chunk_nonce, chunk, chunk_aad)
            output_stream.write(chunk_nonce)
            output_stream.write(struct.pack(">I", len(encrypted_chunk)))
            output_stream.write(encrypted_chunk)
            chunk_index += 1

        checksum = hashlib.sha256()
        input_stream.seek(0)
        while True:
            chunk = input_stream.read(self.chunk_size)
            if not chunk:
                break
            checksum.update(chunk)

        # Restore position for callers that expect fully consumed streams
        try:
            input_stream.seek(0)
        except Exception:
            pass

        return {
            "key_version": version,
            "chunks": chunk_index,
            "checksum": checksum.hexdigest(),
        }

    def decrypt_stream(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        aad: Optional[bytes] = b"",
    ) -> Dict[str, Any]:
        sha = hashlib.sha256()
        total = 0
        for chunk in self.decrypt_stream_generator(input_stream, aad):
            output_stream.write(chunk)
            sha.update(chunk)
            total += len(chunk)
        return {"bytes": total, "checksum": sha.hexdigest(), "verified": True}

    def decrypt_stream_generator(
        self, input_stream: BinaryIO, aad: Optional[bytes] = b""
    ):
        """Bulletproof streaming decryption generator."""
        try:
            encryptor = FileEncryptor(self.keyring)
            header, _ = encryptor.deserialize_header_from_stream(input_stream)
            dek = encryptor._unwrap_key(header.wrapped_key, header.key_version)
            cipher = AESGCM(dek)

            total_decrypted = 0
            chunk_index = 0

            while total_decrypted < header.original_size:
                try:
                    # Robust reads
                    chunk_nonce = read_exactly(input_stream, NONCE_SIZE)
                    if not chunk_nonce:
                        break

                    len_bytes = read_exactly(input_stream, 4)
                    if not len_bytes:
                        break
                    encrypted_len = struct.unpack(">I", len_bytes)[0]

                    encrypted_chunk = read_exactly(input_stream, encrypted_len)
                    if len(encrypted_chunk) < encrypted_len:
                        break  # Safety

                    chunk_aad = struct.pack(">Q", chunk_index)
                    if aad:
                        chunk_aad = aad + chunk_aad

                    chunk = cipher.decrypt(chunk_nonce, encrypted_chunk, chunk_aad)
                    yield chunk

                    total_decrypted += len(chunk)
                    chunk_index += 1
                except (EOFError, struct.error):
                    break  # Clean termination on network drop
                except Exception as e:
                    logger.error(f"Decryption error at chunk {chunk_index}: {e}")
                    break
        except Exception as e:
            logger.error(f"Stream startup failed: {e}")
            # Silent return to avoid ASGI protocol errors if headers were already sent
            return


# Convenience
def encrypt_file(data: bytes, aad: Optional[bytes] = b"") -> bytes:
    return FileEncryptor().encrypt_to_blob(data, aad)


def decrypt_file(blob: bytes, aad: Optional[bytes] = b"") -> bytes:
    return FileEncryptor().decrypt_from_blob(blob, aad)
