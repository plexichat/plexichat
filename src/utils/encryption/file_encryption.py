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
FILE_MAGIC = b"PXENC"  # PlexiChat Encrypted
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

    Each file gets a unique Data Encryption Key (DEK) which is then
    wrapped (encrypted) with the master Key Encryption Key (KEK) from
    the keyring. This allows key rotation without re-encrypting all files.
    """

    def __init__(self, keyring: Optional[Keyring] = None):
        """
        Initialize file encryptor.

        Args:
            keyring: Keyring for master keys. Uses default if None.
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
            self.keyring.get_key()  # Triggers generation
            self.keyring.save()

    def _generate_dek(self) -> bytes:
        """Generate a new Data Encryption Key."""
        return AESGCM.generate_key(bit_length=256)

    def _wrap_key(
        self, dek: bytes, kek_version: Optional[int] = None
    ) -> Tuple[bytes, int]:
        """
        Wrap (encrypt) a DEK with the master KEK.

        Args:
            dek: Data Encryption Key to wrap
            kek_version: KEK version to use (current if None)

        Returns:
            Tuple of (wrapped_key, kek_version)
        """
        version, kek = self.keyring.get_key(kek_version)
        cipher = AESGCM(kek)
        nonce = os.urandom(NONCE_SIZE)
        wrapped = cipher.encrypt(nonce, dek, None)
        # Prepend nonce to wrapped key
        return nonce + wrapped, version

    def _unwrap_key(self, wrapped_key: bytes, kek_version: int) -> bytes:
        """
        Unwrap (decrypt) a DEK using the master KEK.

        Args:
            wrapped_key: Wrapped DEK (nonce + ciphertext)
            kek_version: KEK version used for wrapping

        Returns:
            Unwrapped DEK
        """
        _, kek = self.keyring.get_key(kek_version)
        cipher = AESGCM(kek)
        nonce = wrapped_key[:NONCE_SIZE]
        ciphertext = wrapped_key[NONCE_SIZE:]
        return cipher.decrypt(nonce, ciphertext, None)

    def encrypt(self, data: bytes, aad: Optional[bytes] = None) -> FileEncryptionResult:
        """
        Encrypt file data.

        Args:
            data: Raw file bytes
            aad: Additional Authenticated Data (e.g., file ID)

        Returns:
            FileEncryptionResult with encrypted data and header
        """
        if not data:
            raise ValueError("Cannot encrypt empty data")

        # Generate per-file DEK
        dek = self._generate_dek()

        # Wrap DEK with master key
        wrapped_key, key_version = self._wrap_key(dek)

        # Generate nonce for file encryption
        nonce = os.urandom(NONCE_SIZE)

        # Compute checksum of original data
        checksum = hashlib.sha256(data).hexdigest()

        # Encrypt data
        cipher = AESGCM(dek)
        ciphertext = cipher.encrypt(nonce, data, aad)

        header = EncryptedFileHeader(
            version=FILE_VERSION,
            key_version=key_version,
            wrapped_key=wrapped_key,
            nonce=nonce,
            original_size=len(data),
            checksum=checksum,
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

        Args:
            encrypted_data: Encrypted file bytes
            header: Encryption header with key info
            aad: Additional Authenticated Data used during encryption
            verify_checksum: Whether to verify SHA-256 checksum

        Returns:
            FileDecryptionResult with decrypted data
        """
        # Unwrap DEK
        dek = self._unwrap_key(header.wrapped_key, header.key_version)

        # Decrypt data
        cipher = AESGCM(dek)
        data = cipher.decrypt(header.nonce, encrypted_data, aad)

        # Verify checksum
        verified = True
        if verify_checksum:
            actual_checksum = hashlib.sha256(data).hexdigest()
            verified = actual_checksum == header.checksum
            if not verified:
                logger.warning("File checksum mismatch - data may be corrupted")

        return FileDecryptionResult(
            data=data,
            original_size=len(data),
            verified=verified,
        )

    def serialize_header(self, header: EncryptedFileHeader) -> bytes:
        """
        Serialize encryption header to bytes.

        Format:
        - Magic (5 bytes): "PXENC"
        - Version (1 byte)
        - Key version (4 bytes, big-endian)
        - Wrapped key length (2 bytes, big-endian)
        - Wrapped key (variable)
        - Nonce (12 bytes)
        - Original size (8 bytes, big-endian)
        - Checksum (64 bytes, hex string)
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
        Automatically detects blob (small file) vs stream (large file) format.

        Args:
            data: Bytes starting with header

        Returns:
            Tuple of (header, header_size)
        """
        if len(data) < 12 or data[:5] != FILE_MAGIC:
            raise ValueError("Invalid encrypted file: missing or short magic bytes")

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
            raise ValueError("Truncated encrypted file: missing wrapped key")
            
        wrapped_key = data[offset : offset + wrapped_key_len]
        offset += wrapped_key_len

        # Detection logic:
        # Blob: Nonce(12) + Size(8) + Checksum(64) = 84 total after wrapped key
        # Stream: Size(8) + ChunkSize(4) = 12 total after wrapped key
        remaining_after_key = len(data) - offset
        
        if remaining_after_key < 84:
            # Likely a stream header (8+4 = 12 bytes)
            if remaining_after_key < 12:
                raise ValueError("Truncated stream header")
                
            original_size = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 8
            # Skip chunk_size field
            offset += 4
            
            header = EncryptedFileHeader(
                version=version,
                key_version=key_version,
                wrapped_key=wrapped_key,
                nonce=b"", # Not in stream header
                original_size=original_size,
                checksum="", # Not in stream header
            )
        else:
            # Blob format header (nonce + size + checksum)
            nonce = data[offset : offset + NONCE_SIZE]
            offset += NONCE_SIZE

            original_size = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 8

            checksum = data[offset : offset + 64].decode("ascii")
            offset += 64

            header = EncryptedFileHeader(
                version=version,
                key_version=key_version,
                wrapped_key=wrapped_key,
                nonce=nonce,
                original_size=original_size,
                checksum=checksum,
            )

        return header, offset

    def deserialize_header_from_stream(self, stream: BinaryIO) -> Tuple[EncryptedFileHeader, int]:
        """
        Read and deserialize header directly from a stream.
        EFFICIENT: Reads only exactly what is needed to avoid over-reading.
        """
        # 1. Read fixed prefix: Magic (5) + Version (1) + KeyVersion (4) + KeyLen (2) = 12 bytes
        prefix = stream.read(12)
        if len(prefix) < 12:
            raise ValueError("Truncated header: prefix too short")
            
        if prefix[:5] != FILE_MAGIC:
            raise ValueError("Invalid encrypted file: missing magic bytes")
            
        version = struct.unpack(">B", prefix[5:6])[0]
        key_version = struct.unpack(">I", prefix[6:10])[0]
        wrapped_key_len = struct.unpack(">H", prefix[10:12])[0]
        
        # 2. Read variable part: Wrapped Key (N)
        wrapped_key = stream.read(wrapped_key_len)
        if len(wrapped_key) < wrapped_key_len:
            raise ValueError("Truncated header: wrapped key missing")
            
        # 3. Peek at the next 12 bytes to see if it's a stream or blob
        # Stream header has exactly 12 bytes left (8 size + 4 chunk_size)
        # Blob header has 12 (nonce) + 8 (size) + 64 (checksum) = 84 bytes left
        
        # We read 12 bytes. If it's a stream, we are DONE.
        # If it's a blob, we still have 8 + 64 = 72 bytes to go.
        next_12 = stream.read(12)
        if len(next_12) < 12:
            raise ValueError("Truncated header: format data missing")
            
        # To distinguish, we try to peek/heuristic or just assume based on size.
        # However, the safest way is to check the version/format.
        # For now, we use the fact that the next field in stream is size(8) and chunk_size(4)
        # Whereas in blob it's nonce(12).
        
        # We'll look at the 12 bytes. If they look like a stream header:
        potential_size = struct.unpack(">Q", next_12[:8])[0]
        potential_chunk = struct.unpack(">I", next_12[8:12])[0]
        
        # If chunk size is standard (256KB or 1MB), it's a stream.
        if potential_chunk == CHUNK_SIZE or potential_chunk == 1024*1024:
            header = EncryptedFileHeader(
                version=version,
                key_version=key_version,
                wrapped_key=wrapped_key,
                nonce=b"",
                original_size=potential_size,
                checksum="",
            )
            return header, 12 + wrapped_key_len + 12
        else:
            # Assume it's a blob header. We already read the 12-byte nonce.
            nonce = next_12
            # Read original_size(8) and checksum(64)
            rem = stream.read(8 + 64)
            if len(rem) < 72:
                raise ValueError("Truncated blob header: missing size/checksum")
                
            original_size = struct.unpack(">Q", rem[:8])[0]
            checksum = rem[8:72].decode("ascii")
            
            header = EncryptedFileHeader(
                version=version,
                key_version=key_version,
                wrapped_key=wrapped_key,
                nonce=nonce,
                original_size=original_size,
                checksum=checksum,
            )
            return header, 12 + wrapped_key_len + 12 + 72

    def encrypt_to_blob(self, data: bytes, aad: Optional[bytes] = None) -> bytes:
        """
        Encrypt data and return as single blob with header.

        Args:
            data: Raw file bytes
            aad: Additional Authenticated Data

        Returns:
            Complete encrypted blob (header + ciphertext)
        """
        result = self.encrypt(data, aad)
        header_bytes = self.serialize_header(result.header)
        return header_bytes + result.encrypted_data

    def decrypt_from_blob(
        self, blob: bytes, aad: Optional[bytes] = None, verify_checksum: bool = True
    ) -> bytes:
        """
        Decrypt data from blob with embedded header.

        Args:
            blob: Complete encrypted blob
            aad: Additional Authenticated Data
            verify_checksum: Whether to verify checksum

        Returns:
            Decrypted data
        """
        header, header_size = self.deserialize_header(blob)
        encrypted_data = blob[header_size:]
        result = self.decrypt(encrypted_data, header, aad, verify_checksum)
        return result.data


class StreamingFileEncryptor:
    """
    Streaming encryption for large files.

    Encrypts data in chunks to avoid loading entire file into memory.
    Each chunk is independently authenticated.
    """

    def __init__(self, keyring: Optional[Keyring] = None, chunk_size: int = CHUNK_SIZE):
        """
        Initialize streaming encryptor.

        Args:
            keyring: Keyring for master keys
            chunk_size: Size of each chunk in bytes
        """
        from pathlib import Path

        self.keyring = keyring or Keyring(
            Path.home() / ".plexichat" / "data" / "file_keyring.json",
            env_var="PLEXICHAT_MEDIA_KEY",
        )
        self.chunk_size = chunk_size
        self._ensure_key()

    def _ensure_key(self) -> None:
        """Ensure at least one master key exists."""
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
        """
        Encrypt a stream in chunks.

        Args:
            input_stream: Input file-like object
            output_stream: Output file-like object
            file_size: Total size of input
            aad: Additional Authenticated Data

        Returns:
            Metadata dict with encryption info
        """
        # Generate per-file DEK
        dek = AESGCM.generate_key(bit_length=256)
        cipher = AESGCM(dek)

        # Wrap DEK
        version, kek = self.keyring.get_key()
        kek_cipher = AESGCM(kek)
        wrap_nonce = os.urandom(NONCE_SIZE)
        wrapped_key = wrap_nonce + kek_cipher.encrypt(wrap_nonce, dek, None)

        # Compute checksum while encrypting
        hasher = hashlib.sha256()

        # Write magic and version
        output_stream.write(FILE_MAGIC)
        output_stream.write(struct.pack(">B", FILE_VERSION))
        output_stream.write(struct.pack(">I", version))
        output_stream.write(struct.pack(">H", len(wrapped_key)))
        output_stream.write(wrapped_key)
        output_stream.write(struct.pack(">Q", file_size))
        output_stream.write(struct.pack(">I", self.chunk_size))

        # Encrypt chunks
        chunk_index = 0
        total_encrypted = 0

        while True:
            chunk = input_stream.read(self.chunk_size)
            if not chunk:
                break

            hasher.update(chunk)

            # Each chunk gets unique nonce: base_nonce XOR chunk_index
            chunk_nonce = os.urandom(NONCE_SIZE)

            # Include chunk index in AAD to prevent reordering
            chunk_aad = struct.pack(">Q", chunk_index)
            if aad:
                chunk_aad = aad + chunk_aad

            encrypted_chunk = cipher.encrypt(chunk_nonce, chunk, chunk_aad)

            # Write: nonce (12) + encrypted_chunk (len + 16 tag)
            output_stream.write(chunk_nonce)
            output_stream.write(struct.pack(">I", len(encrypted_chunk)))
            output_stream.write(encrypted_chunk)

            chunk_index += 1
            total_encrypted += len(encrypted_chunk)

        # Write checksum at end
        checksum = hasher.hexdigest()
        output_stream.write(checksum.encode("ascii"))

        return {
            "key_version": version,
            "chunks": chunk_index,
            "checksum": checksum,
            "encrypted_size": total_encrypted,
        }

    def decrypt_stream(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        aad: Optional[bytes] = None,
        verify_checksum: bool = True,
    ) -> Dict[str, Any]:
        """
        Decrypt a stream in chunks.

        Args:
            input_stream: Input file-like object (encrypted)
            output_stream: Output file-like object
            aad: Additional Authenticated Data
            verify_checksum: Whether to verify final checksum

        Returns:
            Metadata dict with decryption info
        """
        # Read header using unified logic
        header, _ = FileEncryptor(self.keyring).deserialize_header_from_stream(input_stream)

        # Unwrap DEK
        _, kek = self.keyring.get_key(header.key_version)
        kek_cipher = AESGCM(kek)
        wrap_nonce = header.wrapped_key[:NONCE_SIZE]
        dek = kek_cipher.decrypt(wrap_nonce, header.wrapped_key[NONCE_SIZE:], None)
        cipher = AESGCM(dek)

        # Decrypt chunks
        hasher = hashlib.sha256()
        chunk_index = 0
        total_decrypted = 0

        while total_decrypted < header.original_size:
            # Read chunk nonce
            chunk_nonce = input_stream.read(NONCE_SIZE)
            if len(chunk_nonce) < NONCE_SIZE:
                break

            # Read encrypted chunk length
            len_bytes = input_stream.read(4)
            if len(len_bytes) < 4:
                break
            encrypted_len = struct.unpack(">I", len_bytes)[0]

            # Read encrypted chunk
            encrypted_chunk = input_stream.read(encrypted_len)
            if len(encrypted_chunk) < encrypted_len:
                logger.error(f"Truncated encrypted data at chunk {chunk_index} (expected {encrypted_len}, got {len(encrypted_chunk)})")
                break # Stop at truncation instead of crashing

            # Reconstruct AAD
            chunk_aad = struct.pack(">Q", chunk_index)
            if aad:
                chunk_aad = aad + chunk_aad

            # Decrypt
            try:
                chunk = cipher.decrypt(chunk_nonce, encrypted_chunk, chunk_aad)
                hasher.update(chunk)
                output_stream.write(chunk)

                chunk_index += 1
                total_decrypted += len(chunk)
            except Exception as e:
                logger.error(f"Decryption failed at chunk {chunk_index}: {e}")
                break

        # Verify checksum
        verified = False
        try:
            stored_checksum = input_stream.read(64).decode("ascii")
            computed_checksum = hasher.hexdigest()
            verified = (stored_checksum == computed_checksum)
            if verify_checksum and not verified:
                logger.warning("Stream checksum mismatch")
        except Exception:
            pass

        return {
            "key_version": header.key_version,
            "chunks": chunk_index,
            "original_size": header.original_size,
            "verified": verified,
        }

    def decrypt_stream_generator(
        self,
        input_stream: BinaryIO,
        aad: Optional[bytes] = None,
        verify_checksum: bool = True,
    ):
        """
        Decrypt a stream in chunks and yield them (generator).

        Args:
            input_stream: Input file-like object (encrypted)
            aad: Additional Authenticated Data
            verify_checksum: Whether to verify final checksum

        Yields:
            Decrypted chunks of data
        """
        # Read header using unified logic
        header, _ = FileEncryptor(self.keyring).deserialize_header_from_stream(input_stream)

        # Unwrap DEK
        _, kek = self.keyring.get_key(header.key_version)
        kek_cipher = AESGCM(kek)
        wrap_nonce = header.wrapped_key[:NONCE_SIZE]
        dek = kek_cipher.decrypt(wrap_nonce, header.wrapped_key[NONCE_SIZE:], None)
        cipher = AESGCM(dek)

        # Decrypt chunks
        hasher = hashlib.sha256()
        chunk_index = 0
        total_decrypted = 0

        while total_decrypted < header.original_size:
            # Read chunk nonce
            chunk_nonce = input_stream.read(NONCE_SIZE)
            if len(chunk_nonce) < NONCE_SIZE:
                break

            # Read encrypted chunk length
            len_bytes = input_stream.read(4)
            if len(len_bytes) < 4:
                break
            encrypted_len = struct.unpack(">I", len_bytes)[0]

            # Read encrypted chunk
            encrypted_chunk = input_stream.read(encrypted_len)
            if len(encrypted_chunk) < encrypted_len:
                logger.error(f"Truncated encrypted stream at chunk {chunk_index} (expected {encrypted_len}, got {len(encrypted_chunk)})")
                break

            # Reconstruct AAD
            chunk_aad = struct.pack(">Q", chunk_index)
            if aad:
                chunk_aad = aad + chunk_aad

            # Decrypt and yield
            try:
                chunk = cipher.decrypt(chunk_nonce, encrypted_chunk, chunk_aad)
                hasher.update(chunk)
                yield chunk

                chunk_index += 1
                total_decrypted += len(chunk)
            except Exception as e:
                logger.error(f"Decryption failed at chunk {chunk_index}: {e}")
                break

        # Verify checksum
        if verify_checksum:
            try:
                stored_checksum = input_stream.read(64).decode("ascii")
                computed_checksum = hasher.hexdigest()
                if stored_checksum and stored_checksum != computed_checksum:
                    logger.warning("Stream checksum mismatch")
            except Exception:
                pass


# Module-level convenience functions
_file_encryptor: Optional[FileEncryptor] = None
_streaming_encryptor: Optional[StreamingFileEncryptor] = None


def get_file_encryptor() -> FileEncryptor:
    """Get or create the file encryptor instance."""
    global _file_encryptor
    if _file_encryptor is None:
        _file_encryptor = FileEncryptor()
    return _file_encryptor


def get_streaming_encryptor() -> StreamingFileEncryptor:
    """Get or create the streaming encryptor instance."""
    global _streaming_encryptor
    if _streaming_encryptor is None:
        _streaming_encryptor = StreamingFileEncryptor()
    return _streaming_encryptor


def encrypt_file(data: bytes, aad: Optional[bytes] = None) -> bytes:
    """Encrypt file data to blob."""
    return get_file_encryptor().encrypt_to_blob(data, aad)


def decrypt_file(blob: bytes, aad: Optional[bytes] = None) -> bytes:
    """Decrypt file data from blob."""
    return get_file_encryptor().decrypt_from_blob(blob, aad)
