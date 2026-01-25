"""
Encrypted storage backend wrapper.

Wraps any storage backend to provide transparent client-side encryption.
Files are encrypted before upload and decrypted after download.
"""

import io
from typing import BinaryIO, Tuple, Optional

import utils.logger as logger

from .base import StorageBackendBase
from ..exceptions import StorageReadError, StorageWriteError


# Threshold for streaming encryption (files larger than this use streaming)
STREAMING_THRESHOLD = 10 * 1024 * 1024  # 10MB


class EncryptedStorage(StorageBackendBase):
    """
    Storage backend wrapper that encrypts files at rest.

    Wraps any StorageBackendBase implementation to provide transparent
    AES-256-GCM encryption. Each file gets a unique encryption key that
    is wrapped with a master key from the keyring.
    """

    def __init__(
        self,
        backend: StorageBackendBase,
        enabled: bool = True,
        streaming_threshold: int = STREAMING_THRESHOLD,
    ):
        """
        Initialize encrypted storage.

        Args:
            backend: Underlying storage backend
            enabled: Whether encryption is enabled
            streaming_threshold: Size threshold for streaming encryption
        """
        self._backend = backend
        self._enabled = enabled
        self._streaming_threshold = streaming_threshold
        self._encryptor = None
        self._streaming_encryptor = None

        if enabled:
            self._init_encryptors()

    def _init_encryptors(self) -> None:
        """Initialize encryption components."""
        try:
            from src.utils.encryption.file_encryption import (
                FileEncryptor,
                StreamingFileEncryptor,
            )

            self._encryptor = FileEncryptor()
            self._streaming_encryptor = StreamingFileEncryptor()
            logger.info("File encryption initialized for storage")
        except Exception as e:
            logger.error(f"Failed to initialize file encryption: {e}")
            self._enabled = False

    def _get_aad(self, path: str) -> bytes:
        """
        Generate Additional Authenticated Data from path.

        This binds the encrypted data to the storage path, preventing
        an attacker from moving encrypted files between paths.
        """
        return path.encode("utf-8")

    def store(self, file_data: bytes, path: str, content_type: str) -> str:
        """Store encrypted file data."""
        if not self._enabled or not self._encryptor:
            return self._backend.store(file_data, path, content_type)

        try:
            aad = self._get_aad(path)
            encrypted_blob = self._encryptor.encrypt_to_blob(file_data, aad)

            # Store with .enc suffix to indicate encryption
            enc_path = path + ".enc"
            self._backend.store(
                encrypted_blob, enc_path, "application/octet-stream"
            )

            logger.debug(
                f"Stored encrypted file: {path} ({len(file_data)} -> {len(encrypted_blob)} bytes)"
            )
            return path  # Return original path (we handle .enc internally)

        except Exception as e:
            logger.error(f"Encryption failed for {path}: {e}")
            raise StorageWriteError(f"Encryption failed: {e}", "encrypted")

    def store_stream(
        self, stream: BinaryIO, path: str, content_type: str, size: int
    ) -> str:
        """Store encrypted file from stream."""
        if not self._enabled:
            return self._backend.store_stream(stream, path, content_type, size)

        # For small files, read into memory and use regular encryption
        if size <= self._streaming_threshold:
            data = stream.read()
            return self.store(data, path, content_type)

        # For large files, use streaming encryption
        if not self._streaming_encryptor:
            # Fallback to reading into memory
            data = stream.read()
            return self.store(data, path, content_type)

        try:
            aad = self._get_aad(path)
            output = io.BytesIO()

            self._streaming_encryptor.encrypt_stream(stream, output, size, aad)

            output.seek(0)
            enc_path = path + ".enc"
            encrypted_size = output.getbuffer().nbytes

            self._backend.store_stream(
                output, enc_path, "application/octet-stream", encrypted_size
            )

            logger.debug(
                f"Stored encrypted stream: {path} ({size} -> {encrypted_size} bytes)"
            )
            return path

        except Exception as e:
            logger.error(f"Stream encryption failed for {path}: {e}")
            raise StorageWriteError(f"Stream encryption failed: {e}", "encrypted")

    def retrieve(self, path: str) -> bytes:
        """Retrieve and decrypt file data."""
        # Try encrypted path first
        enc_path = path + ".enc"

        try:
            if self._backend.exists(enc_path):
                if not self._enabled or not self._encryptor:
                    raise StorageReadError(
                        "File is encrypted but encryption is disabled", "encrypted"
                    )

                encrypted_blob = self._backend.retrieve(enc_path)
                aad = self._get_aad(path)

                data = self._encryptor.decrypt_from_blob(encrypted_blob, aad)
                logger.debug(f"Retrieved and decrypted: {path}")
                return data
        except StorageReadError:
            pass  # File doesn't exist with .enc, try unencrypted
        except Exception as e:
            logger.error(f"Decryption failed for {path}: {e}")
            raise StorageReadError(f"Decryption failed: {e}", "encrypted")

        # Fall back to unencrypted path (for legacy files)
        return self._backend.retrieve(path)

    def retrieve_stream(self, path: str) -> Tuple[BinaryIO, int]:
        """Retrieve file as decrypted stream."""
        # Try encrypted path first
        enc_path = path + ".enc"

        if self._backend.exists(enc_path):
            if not self._enabled or not self._encryptor:
                raise StorageReadError(
                    "File is encrypted but encryption is disabled", "encrypted"
                )

            # Get size of original file (from header)
            original_size = self.get_size(path)

            # For small files, use regular retrieve
            if original_size <= self._streaming_threshold:
                data = self.retrieve(path)
                return io.BytesIO(data), len(data)

            # For large files, use streaming decryption
            if self._streaming_encryptor:
                try:
                    enc_stream, _ = self._backend.retrieve_stream(enc_path)
                    aad = self._get_aad(path)
                    
                    # Return the generator directly. FastAPI StreamingResponse handles iterables.
                    # Note: We return it as the 'stream' part of the tuple.
                    generator = self._streaming_encryptor.decrypt_stream_generator(enc_stream, aad)
                    return generator, original_size
                except Exception as e:
                    logger.error(f"Stream decryption failed for {path}: {e}")
                    # Fallback to retrieve if streaming fails
            
            data = self.retrieve(path)
            return io.BytesIO(data), len(data)

        # Fall back to unencrypted path
        return self._backend.retrieve_stream(path)

    def delete(self, path: str) -> bool:
        """Delete file (tries both encrypted and unencrypted paths)."""
        enc_path = path + ".enc"

        deleted = False

        # Try to delete encrypted version
        try:
            if self._backend.exists(enc_path):
                deleted = self._backend.delete(enc_path)
        except Exception:
            pass

        # Also try unencrypted version (for cleanup)
        try:
            if self._backend.exists(path):
                deleted = self._backend.delete(path) or deleted
        except Exception:
            pass

        return deleted

    def exists(self, path: str) -> bool:
        """Check if file exists (encrypted or unencrypted)."""
        enc_path = path + ".enc"
        return self._backend.exists(enc_path) or self._backend.exists(path)

    def get_url(self, path: str) -> str:
        """
        Get URL for file.

        Note: For encrypted files, this returns a URL that requires
        server-side decryption. Direct access to the encrypted blob
        would return unusable data.
        """
        # Return the logical path URL, not the .enc path
        # The serving layer must handle decryption
        return self._backend.get_url(path)

    def get_size(self, path: str) -> int:
        """Get original (unencrypted) file size."""
        enc_path = path + ".enc"

        if self._backend.exists(enc_path):
            # For encrypted files, we need to read the header to get original size
            if self._encryptor:
                try:
                    # Read just enough for the header
                    encrypted_blob = self._backend.retrieve(enc_path)
                    header, _ = self._encryptor.deserialize_header(encrypted_blob)
                    return header.original_size
                except Exception:
                    pass
            # Fallback: return encrypted size (not accurate but better than nothing)
            return self._backend.get_size(enc_path)

        return self._backend.get_size(path)

    def is_encrypted(self, path: str) -> bool:
        """Check if a file is stored encrypted."""
        enc_path = path + ".enc"
        return self._backend.exists(enc_path)

    def generate_presigned_url(
        self, path: str, expires_in: int = 3600, params: Optional[dict] = None
    ) -> str:
        """
        Generate a presigned URL.
        Only works if the file is NOT encrypted.
        """
        if self.is_encrypted(path):
            raise RuntimeError("Cannot generate presigned URL for encrypted file")

        if hasattr(self._backend, "generate_presigned_url"):
            return self._backend.generate_presigned_url(path, expires_in, params)

        raise RuntimeError("Underlying backend does not support presigned URLs")

    def get_metadata(self, path: str) -> dict:
        """Get file metadata including encryption status."""
        base_meta = {
            "path": path,
            "exists": self.exists(path),
            "encrypted": self.is_encrypted(path),
        }

        if base_meta["exists"]:
            base_meta["size"] = self.get_size(path)

        return base_meta


def wrap_storage_with_encryption(
    backend: StorageBackendBase, enabled: bool = True
) -> StorageBackendBase:
    """
    Wrap a storage backend with encryption.

    Args:
        backend: Storage backend to wrap
        enabled: Whether encryption is enabled

    Returns:
        EncryptedStorage wrapper or original backend if disabled
    """
    if not enabled:
        return backend

    return EncryptedStorage(backend, enabled=True)
