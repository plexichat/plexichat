"""
Base storage backend interface.
"""

from abc import ABC, abstractmethod
from typing import BinaryIO, Tuple


class StorageBackendBase(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    def store(self, file_data: bytes, path: str, content_type: str) -> str:
        """
        Store file data at the specified path.

        Args:
            file_data: Raw file bytes
            path: Storage path (relative)
            content_type: MIME type of the file

        Returns:
            Full storage path or URL
        """
        pass

    @abstractmethod
    def store_stream(
        self, stream: BinaryIO, path: str, content_type: str, size: int
    ) -> str:
        """
        Store file from a stream.

        Args:
            stream: File-like object to read from
            path: Storage path (relative)
            content_type: MIME type of the file
            size: Expected file size in bytes

        Returns:
            Full storage path or URL
        """
        pass

    @abstractmethod
    def retrieve(self, path: str) -> bytes:
        """
        Retrieve file data from storage.

        Args:
            path: Storage path

        Returns:
            Raw file bytes
        """
        pass

    @abstractmethod
    def retrieve_stream(self, path: str) -> Tuple[BinaryIO, int]:
        """
        Retrieve file as a stream.

        Args:
            path: Storage path

        Returns:
            Tuple of (file-like object, size)
        """
        pass

    @abstractmethod
    def delete(self, path: str) -> bool:
        """
        Delete file from storage.

        Args:
            path: Storage path

        Returns:
            True if deleted successfully
        """
        pass

    @abstractmethod
    def exists(self, path: str) -> bool:
        """
        Check if file exists in storage.

        Args:
            path: Storage path

        Returns:
            True if file exists
        """
        pass

    @abstractmethod
    def get_url(self, path: str) -> str:
        """
        Get public URL for file.

        Args:
            path: Storage path

        Returns:
            Public URL
        """
        pass

    @abstractmethod
    def get_size(self, path: str) -> int:
        """
        Get file size.

        Args:
            path: Storage path

        Returns:
            File size in bytes
        """
        pass

    @abstractmethod
    def is_encrypted(self, path: str) -> bool:
        """
        Check if a file is stored encrypted.

        Args:
            path: Storage path

        Returns:
            True if encrypted
        """
        pass

    def get_metadata(self, path: str) -> dict:
        """
        Get file metadata.

        Args:
            path: Storage path

        Returns:
            Metadata dictionary
        """
        return {
            "path": path,
            "size": self.get_size(path),
            "exists": self.exists(path),
        }
