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
FILE_MAGIC = b"PXENC"  # PlexiChat Encrypted (Common)
STREAM_MAGIC = b"PXSTR" # PlexiChat Stream (Robust)
FILE_VERSION = 1
CHUNK_SIZE = 256 * 1024  # 256KB chunks for streaming
NONCE_SIZE = 12
TAG_SIZE = 16
KEY_SIZE = 32


@dataclass
class EncryptedFileHeader:
    """Header for encrypted files."""

    version: int
    key_version: int
    wrapped_key: bytes
    nonce: bytes
    original_size: int
    checksum: str  # SHA-256 of original content
    is_stream: bool = False


def read_exactly(stream: BinaryIO, n: int) -> bytes:
    """
    Read exactly n bytes from a stream.
    Handles fragmentation (especially important for S3/network streams).
    """
    result = b""
    while len(result) < n:
        chunk = stream.read(n - len(result))
        if not chunk:
            break
        result += chunk
    return result


class FileEncryptor:
    """
    Encrypts and decrypts files using AES-256-GCM.
    """

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

    def _wrap_key(self, dek: bytes, kek_version: Optional[int] = None) -> Tuple[bytes, int]:
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

    def encrypt_to_blob(self, data: bytes, aad: Optional[bytes] = None) -> bytes:
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
        """
        Deserialize encryption header from bytes.
        """
        if len(data) < 12:
            raise ValueError("Invalid encrypted file: data too short")
            
        magic = data[:5]
        if magic not in (FILE_MAGIC, STREAM_MAGIC):
            raise ValueError(f"Invalid encrypted file: missing magic bytes (got {magic})")

        offset = 5
        version = struct.unpack(">B", data[offset : offset + 1])[0]
        offset += 1
        key_version = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        wrapped_key_len = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2

        if len(data) < offset + wrapped_key_len:
            raise ValueError("Truncated header: wrapped key missing")
            
        wrapped_key = data[offset : offset + wrapped_key_len]
        offset += wrapped_key_len

        # Stream vs Blob detection
        is_stream = False
        if magic == STREAM_MAGIC:
            is_stream = True
        elif magic == FILE_MAGIC:
            # Heuristic for legacy PXENC magic
            # Stream header has exactly 12 bytes after key
            # Blob header has exactly 84 bytes after key
            remaining = len(data) - offset
            if remaining >= 12:
                # Check for standard chunk size at expected offset (+8)
                potential_chunk_size = struct.unpack(">I", data[offset+8 : offset+12])[0]
                if potential_chunk_size in (262144, 524288, 1048576):
                    is_stream = True

        if is_stream:
            original_size = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 12
            return EncryptedFileHeader(
                version=version, key_version=key_version, wrapped_key=wrapped_key,
                nonce=b"", original_size=original_size, checksum="", is_stream=True
            ), offset
        else:
            nonce = data[offset : offset + NONCE_SIZE]
            offset += NONCE_SIZE
            original_size = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 8
            checksum = data[offset : offset + 64].decode("ascii")
            offset += 64
            return EncryptedFileHeader(
                version=version, key_version=key_version, wrapped_key=wrapped_key,
                nonce=nonce, original_size=original_size, checksum=checksum, is_stream=False
            ), offset

    def deserialize_header_from_stream(self, stream: BinaryIO) -> Tuple[EncryptedFileHeader, int]:
        """
        Read and deserialize header directly from a stream using robust reading.
        """
        fixed = read_exactly(stream, 12)
        if len(fixed) < 12: raise ValueError("Truncated header prefix")
        
        magic = fixed[:5]
        wrapped_key_len = struct.unpack(">H", fixed[10:12])[0]
        wrapped_key = read_exactly(stream, wrapped_key_len)
        if len(wrapped_key) < wrapped_key_len: raise ValueError("Truncated wrapped key")
        
        if magic == STREAM_MAGIC:
            rem = read_exactly(stream, 12)
            header_data = fixed + wrapped_key + rem
        else:
            # Heuristic for PXENC
            rem_start = read_exactly(stream, 12)
            if len(rem_start) < 12: raise ValueError("Truncated format data")
            
            potential_chunk = struct.unpack(">I", rem_start[8:12])[0]
            if potential_chunk in (262144, 524288, 1048576):
                header_data = fixed + wrapped_key + rem_start
            else:
                rem_end = read_exactly(stream, 72)
                if len(rem_end) < 72: raise ValueError("Truncated blob data")
                header_data = fixed + wrapped_key + rem_start + rem_end
                
        return self.deserialize_header(header_data)

    def decrypt_from_blob(self, blob: bytes, aad: Optional[bytes] = None) -> bytes:
        header, header_size = self.deserialize_header(blob)
        ciphertext = blob[header_size:]
        dek = self._unwrap_key(header.wrapped_key, header.key_version)
        cipher = AESGCM(dek)
        return cipher.decrypt(header.nonce, ciphertext, aad)


class StreamingFileEncryptor:
    """
    Streaming encryption for large files.
    """

    def __init__(self, keyring: Optional[Keyring] = None, chunk_size: int = CHUNK_SIZE):
        from pathlib import Path
        self.keyring = keyring or Keyring(
            Path.home() / ".plexichat" / "data" / "file_keyring.json",
            env_var="PLEXICHAT_MEDIA_KEY",
        )
        self.chunk_size = chunk_size

    def encrypt_stream(self, input_stream: BinaryIO, output_stream: BinaryIO, file_size: int, aad: Optional[bytes] = None) -> Dict[str, Any]:
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
        total_encrypted = 0
        while True:
            chunk = input_stream.read(self.chunk_size)
            if not chunk: break
            chunk_nonce = os.urandom(NONCE_SIZE)
            chunk_aad = struct.pack(">Q", chunk_index)
            if aad: chunk_aad = aad + chunk_aad
            encrypted_chunk = cipher.encrypt(chunk_nonce, chunk, chunk_aad)
            output_stream.write(chunk_nonce)
            output_stream.write(struct.pack(">I", len(encrypted_chunk)))
            output_stream.write(encrypted_chunk)
            chunk_index += 1
            total_encrypted += len(encrypted_chunk)

        return {"key_version": version, "chunks": chunk_index}

    def decrypt_stream_generator(self, input_stream: BinaryIO, aad: Optional[bytes] = None):
        """Decrypt in chunks (generator) with improved reliability."""
        try:
            encryptor = FileEncryptor(self.keyring)
            header, _ = encryptor.deserialize_header_from_stream(input_stream)
            
            dek = encryptor._unwrap_key(header.wrapped_key, header.key_version)
            cipher = AESGCM(dek)

            chunk_index = 0
            total_decrypted = 0
            
            while total_decrypted < header.original_size:
                # Use read_exactly for robustness
                chunk_nonce = read_exactly(input_stream, NONCE_SIZE)
                if len(chunk_nonce) < NONCE_SIZE: break

                len_bytes = read_exactly(input_stream, 4)
                if len(len_bytes) < 4: break
                encrypted_len = struct.unpack(">I", len_bytes)[0]

                encrypted_chunk = read_exactly(input_stream, encrypted_len)
                if len(encrypted_chunk) < encrypted_len:
                    logger.error(f"Stream truncated: expected {encrypted_len} but got {len(encrypted_chunk)}")
                    break

                chunk_aad = struct.pack(">Q", chunk_index)
                if aad: chunk_aad = aad + chunk_aad

                try:
                    chunk = cipher.decrypt(chunk_nonce, encrypted_chunk, chunk_aad)
                    yield chunk
                    chunk_index += 1
                    total_decrypted += len(chunk)
                except Exception as e:
                    logger.error(f"Decryption failed at chunk {chunk_index}: {e}")
                    break
        except Exception as e:
            logger.error(f"Stream decryption startup failed: {e}")
            raise


# Convenience functions
def encrypt_file(data: bytes, aad: Optional[bytes] = None) -> bytes:
    return FileEncryptor().encrypt_to_blob(data, aad)

def decrypt_file(blob: bytes, aad: Optional[bytes] = None) -> bytes:
    return FileEncryptor().decrypt_from_blob(blob, aad)
