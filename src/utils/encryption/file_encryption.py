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


@dataclass
class FileEncryptionResult:
    """Result of file encryption."""

    encrypted_data: bytes
    header: EncryptedFileHeader
    encrypted_size: int


@dataclass
class FileDecryptionResult:
    """Result of file decryption."""

    data: bytes
    original_size: int
    verified: bool


class FileEncryptor:
    """
    Encrypts and decrypts files using AES-256-GCM.
    """

    def __init__(self, keyring: Optional[Keyring] = None):
        """
        Initialize file encryptor.
        """
        from pathlib import Path

        self.keyring = keyring or Keyring(
            Path.home() / ".plexichat" / "data" / "file_keyring.json",
            env_var="PLEXICHAT_MEDIA_KEY",
        )
        self._ensure_key()

    def _ensure_key(self) -> None:
        """Ensure at least one master key exists."""
        if not self.keyring.keys:
            self.keyring.get_key()
            self.keyring.save()

    def _generate_dek(self) -> bytes:
        """Generate a new Data Encryption Key."""
        return AESGCM.generate_key(bit_length=256)

    def _wrap_key(
        self, dek: bytes, kek_version: Optional[int] = None
    ) -> Tuple[bytes, int]:
        """
        Wrap (encrypt) a DEK with the master KEK.
        """
        version, kek = self.keyring.get_key(kek_version)
        cipher = AESGCM(kek)
        nonce = os.urandom(NONCE_SIZE)
        wrapped = cipher.encrypt(nonce, dek, None)
        return nonce + wrapped, version

    def _unwrap_key(self, wrapped_key: bytes, kek_version: int) -> bytes:
        """
        Unwrap (decrypt) a DEK using the master KEK.
        """
        _, kek = self.keyring.get_key(kek_version)
        cipher = AESGCM(kek)
        nonce = wrapped_key[:NONCE_SIZE]
        ciphertext = wrapped_key[NONCE_SIZE:]
        return cipher.decrypt(nonce, ciphertext, None)

    def encrypt(self, data: bytes, aad: Optional[bytes] = None) -> FileEncryptionResult:
        """
        Encrypt file data.
        """
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
            is_stream=False
        )

        return FileEncryptionResult(
            encrypted_data=ciphertext,
            header=header,
            encrypted_size=len(ciphertext),
        )

    def decrypt(
        self,
        encrypted_data: bytes,
        header: EncryptedFileHeader,
        aad: Optional[bytes] = None,
        verify_checksum: bool = True,
    ) -> FileDecryptionResult:
        """
        Decrypt file data.
        """
        dek = self._unwrap_key(header.wrapped_key, header.key_version)
        cipher = AESGCM(dek)
        data = cipher.decrypt(header.nonce, encrypted_data, aad)

        verified = True
        if verify_checksum:
            actual_checksum = hashlib.sha256(data).hexdigest()
            verified = actual_checksum == header.checksum
            if not verified:
                logger.warning("File checksum mismatch")

        return FileDecryptionResult(
            data=data,
            original_size=len(data),
            verified=verified,
        )

    def serialize_header(self, header: EncryptedFileHeader) -> bytes:
        """
        Serialize encryption header to bytes.
        """
        wrapped_key_len = len(header.wrapped_key)
        checksum_bytes = header.checksum.encode("ascii")

        return (
            FILE_MAGIC
            + struct.pack(">B", header.version)
            + struct.pack(">I", header.key_version)
            + struct.pack(">H", wrapped_key_len)
            + header.wrapped_key
            + header.nonce
            + struct.pack(">Q", header.original_size)
            + checksum_bytes
        )

    def deserialize_header(self, data: bytes) -> Tuple[EncryptedFileHeader, int]:
        """
        Deserialize encryption header from bytes.
        Differentiates between Blob and Stream formats using field analysis.
        """
        if len(data) < 12:
            raise ValueError("Invalid encrypted file: data too short")
            
        magic = data[:5]
        if magic not in (FILE_MAGIC, STREAM_MAGIC):
            raise ValueError("Invalid encrypted file: missing magic bytes")

        offset = 5
        version = struct.unpack(">B", data[offset : offset + 1])[0]
        offset += 1

        if version != FILE_VERSION:
            raise ValueError(f"Unsupported encryption version: {version}")

        key_version = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4

        wrapped_key_len = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2

        if len(data) < offset + wrapped_key_len:
            raise ValueError("Truncated header: wrapped key missing")
            
        wrapped_key = data[offset : offset + wrapped_key_len]
        offset += wrapped_key_len

        # Format analysis after Key(N)
        # Magic PXSTR -> Always Stream
        if magic == STREAM_MAGIC:
            if len(data) < offset + 12:
                raise ValueError("Truncated stream header")
            original_size = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 12 # 8 size + 4 chunk_size
            return EncryptedFileHeader(
                version=version, key_version=key_version, wrapped_key=wrapped_key,
                nonce=b"", original_size=original_size, checksum="", is_stream=True
            ), offset

        # Magic PXENC -> Check heuristic
        # We need at least 12 bytes to check if it's a stream (8 size + 4 chunk)
        if len(data) < offset + 12:
            raise ValueError("Data too short to determine header format")
            
        # The 4 bytes at +8 are chunk_size in Stream format
        # In Blob format, they are the last 4 bytes of the 12-byte random nonce
        potential_chunk_size = struct.unpack(">I", data[offset+8 : offset+12])[0]
        
        # DEFINITIVE: Stream format if chunk size is a known standard
        is_stream = potential_chunk_size in (262144, 524288, 1048576, 2097152)
        
        if is_stream:
            original_size = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 12
            header = EncryptedFileHeader(
                version=version, key_version=key_version, wrapped_key=wrapped_key,
                nonce=b"", original_size=original_size, checksum="", is_stream=True
            )
        else:
            # Assume Blob (Nonce 12 + Size 8 + Checksum 64 = 84 total)
            if len(data) < offset + 84:
                # If we don't have 84 bytes, it MIGHT be a truncated blob
                # but if it was a stream we'd have caught it above.
                raise ValueError(f"Truncated blob header (got {len(data)-offset}, expected 84)")
                
            nonce = data[offset : offset + NONCE_SIZE]
            offset += NONCE_SIZE
            original_size = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 8
            checksum = data[offset : offset + 64].decode("ascii")
            offset += 64
            header = EncryptedFileHeader(
                version=version, key_version=key_version, wrapped_key=wrapped_key,
                nonce=nonce, original_size=original_size, checksum=checksum, is_stream=False
            )

        return header, offset

    def deserialize_header_from_stream(self, stream: BinaryIO) -> Tuple[EncryptedFileHeader, int]:
        """
        Read and deserialize header directly from a stream.
        """
        fixed = stream.read(12)
        if len(fixed) < 12:
            raise ValueError("Truncated header: prefix too short")
        
        magic = fixed[:5]
        wrapped_key_len = struct.unpack(">H", fixed[10:12])[0]
        wrapped_key = stream.read(wrapped_key_len)
        if len(wrapped_key) < wrapped_key_len:
            raise ValueError("Truncated header: key missing")
        
        if magic == STREAM_MAGIC:
            rem = stream.read(12)
            header_data = fixed + wrapped_key + rem
        else:
            # Heuristic read for PXENC
            rem_start = stream.read(12)
            if len(rem_start) < 12:
                raise ValueError("Truncated header: format data missing")
                
            potential_chunk = struct.unpack(">I", rem_start[8:12])[0]
            if potential_chunk in (262144, 524288, 1048576, 2097152):
                header_data = fixed + wrapped_key + rem_start
            else:
                # Read rest of blob header
                rem_end = stream.read(72)
                if len(rem_end) < 72:
                    raise ValueError("Truncated header: blob data missing")
                header_data = fixed + wrapped_key + rem_start + rem_end
                
        return self.deserialize_header(header_data)

    def encrypt_to_blob(self, data: bytes, aad: Optional[bytes] = None) -> bytes:
        """Single blob encryption."""
        result = self.encrypt(data, aad)
        header_bytes = self.serialize_header(result.header)
        return header_bytes + result.encrypted_data

    def decrypt_from_blob(
        self, blob: bytes, aad: Optional[bytes] = None, verify_checksum: bool = True
    ) -> bytes:
        """Single blob decryption."""
        header, header_size = self.deserialize_header(blob)
        encrypted_data = blob[header_size:]
        result = self.decrypt(encrypted_data, header, aad, verify_checksum)
        return result.data


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
        self._ensure_key()

    def _ensure_key(self) -> None:
        if not self.keyring.keys:
            self.keyring.get_key()
            self.keyring.save()

    def encrypt_stream(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        file_size: int,
        aad: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Encrypt in chunks."""
        dek = AESGCM.generate_key(bit_length=256)
        cipher = AESGCM(dek)
        version, kek = self.keyring.get_key()
        kek_cipher = AESGCM(kek)
        wrap_nonce = os.urandom(NONCE_SIZE)
        wrapped_key = wrap_nonce + kek_cipher.encrypt(wrap_nonce, dek, None)

        hasher = hashlib.sha256()
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
            hasher.update(chunk)
            chunk_nonce = os.urandom(NONCE_SIZE)
            chunk_aad = struct.pack(">Q", chunk_index)
            if aad: chunk_aad = aad + chunk_aad
            encrypted_chunk = cipher.encrypt(chunk_nonce, chunk, chunk_aad)
            output_stream.write(chunk_nonce)
            output_stream.write(struct.pack(">I", len(encrypted_chunk)))
            output_stream.write(encrypted_chunk)
            chunk_index += 1
            total_encrypted += len(encrypted_chunk)

        checksum = hasher.hexdigest()
        output_stream.write(checksum.encode("ascii"))
        return {"key_version": version, "chunks": chunk_index, "checksum": checksum}

    def decrypt_stream_generator(
        self,
        input_stream: BinaryIO,
        aad: Optional[bytes] = None,
        verify_checksum: bool = True,
    ):
        """Decrypt in chunks (generator)."""
        header, _ = FileEncryptor(self.keyring).deserialize_header_from_stream(input_stream)
        _, kek = self.keyring.get_key(header.key_version)
        kek_cipher = AESGCM(kek)
        wrap_nonce = header.wrapped_key[:NONCE_SIZE]
        dek = kek_cipher.decrypt(wrap_nonce, header.wrapped_key[NONCE_SIZE:], None)
        cipher = AESGCM(dek)

        chunk_index = 0
        total_decrypted = 0
        while total_decrypted < header.original_size:
            chunk_nonce = input_stream.read(NONCE_SIZE)
            if len(chunk_nonce) < NONCE_SIZE: break
            len_bytes = input_stream.read(4)
            if len(len_bytes) < 4: break
            encrypted_len = struct.unpack(">I", len_bytes)[0]
            encrypted_chunk = input_stream.read(encrypted_len)
            if len(encrypted_chunk) < encrypted_len: break
            chunk_aad = struct.pack(">Q", chunk_index)
            if aad: chunk_aad = aad + chunk_aad
            try:
                chunk = cipher.decrypt(chunk_nonce, encrypted_chunk, chunk_aad)
                yield chunk
                chunk_index += 1
                total_decrypted += len(chunk)
            except Exception: break


# Convenience functions
def encrypt_file(data: bytes, aad: Optional[bytes] = None) -> bytes:
    return FileEncryptor().encrypt_to_blob(data, aad)

def decrypt_file(blob: bytes, aad: Optional[bytes] = None) -> bytes:
    return FileEncryptor().decrypt_from_blob(blob, aad)
