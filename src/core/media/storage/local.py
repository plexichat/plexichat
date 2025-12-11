"""
Local filesystem storage backend.
"""

import os
from typing import BinaryIO, Tuple

import utils.logger as logger

from .base import StorageBackendBase
from ..exceptions import (
    StorageError,
    StorageWriteError,
    StorageReadError,
    StorageDeleteError,
)


class LocalStorage(StorageBackendBase):
    """Local filesystem storage backend."""

    def __init__(self, base_path: str, base_url: str = "/media"):
        """
        Initialize local storage.
        
        Args:
            base_path: Base directory for file storage
            base_url: Base URL prefix for serving files
        """
        self._base_path = os.path.abspath(base_path)
        self._base_url = base_url.rstrip("/")

        os.makedirs(self._base_path, exist_ok=True)
        logger.debug(f"LocalStorage initialized at {self._base_path}")

    def _full_path(self, path: str) -> str:
        """Get full filesystem path."""
        clean_path = path.lstrip("/").lstrip("\\")
        full = os.path.normpath(os.path.join(self._base_path, clean_path))
        if not full.startswith(self._base_path):
            raise StorageError("Invalid path: path traversal detected", "local")
        return full

    def _ensure_dir(self, file_path: str):
        """Ensure directory exists for file."""
        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

    def store(self, file_data: bytes, path: str, content_type: str) -> str:
        """Store file data at the specified path."""
        full_path = self._full_path(path)

        try:
            self._ensure_dir(full_path)
            with open(full_path, "wb") as f:
                f.write(file_data)
            logger.debug(f"Stored file at {full_path} ({len(file_data)} bytes)")
            return path
        except OSError as e:
            logger.error(f"Failed to store file at {full_path}: {e}")
            raise StorageWriteError(f"Failed to write file: {e}", "local")

    def store_stream(self, stream: BinaryIO, path: str, content_type: str, size: int) -> str:
        """Store file from a stream."""
        full_path = self._full_path(path)

        try:
            self._ensure_dir(full_path)
            bytes_written = 0
            with open(full_path, "wb") as f:
                chunk_size = 8192
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_written += len(chunk)
            logger.debug(f"Stored stream at {full_path} ({bytes_written} bytes)")
            return path
        except OSError as e:
            logger.error(f"Failed to store stream at {full_path}: {e}")
            raise StorageWriteError(f"Failed to write file: {e}", "local")

    def retrieve(self, path: str) -> bytes:
        """Retrieve file data from storage."""
        full_path = self._full_path(path)

        try:
            with open(full_path, "rb") as f:
                data = f.read()
            logger.debug(f"Retrieved file from {full_path} ({len(data)} bytes)")
            return data
        except FileNotFoundError:
            raise StorageReadError(f"File not found: {path}", "local")
        except OSError as e:
            logger.error(f"Failed to read file at {full_path}: {e}")
            raise StorageReadError(f"Failed to read file: {e}", "local")

    def retrieve_stream(self, path: str) -> Tuple[BinaryIO, int]:
        """Retrieve file as a stream."""
        full_path = self._full_path(path)

        try:
            size = os.path.getsize(full_path)
            stream = open(full_path, "rb")
            return stream, size
        except FileNotFoundError:
            raise StorageReadError(f"File not found: {path}", "local")
        except OSError as e:
            logger.error(f"Failed to open stream at {full_path}: {e}")
            raise StorageReadError(f"Failed to read file: {e}", "local")

    def delete(self, path: str) -> bool:
        """Delete file from storage."""
        full_path = self._full_path(path)

        try:
            if os.path.exists(full_path):
                os.remove(full_path)
                logger.debug(f"Deleted file at {full_path}")
                return True
            return False
        except OSError as e:
            logger.error(f"Failed to delete file at {full_path}: {e}")
            raise StorageDeleteError(f"Failed to delete file: {e}", "local")

    def exists(self, path: str) -> bool:
        """Check if file exists in storage."""
        full_path = self._full_path(path)
        return os.path.isfile(full_path)

    def get_url(self, path: str) -> str:
        """Get public URL for file."""
        clean_path = path.lstrip("/").lstrip("\\")
        return f"{self._base_url}/{clean_path}"

    def get_size(self, path: str) -> int:
        """Get file size."""
        full_path = self._full_path(path)

        try:
            return os.path.getsize(full_path)
        except OSError:
            return 0

    def get_metadata(self, path: str) -> dict:
        """Get file metadata."""
        full_path = self._full_path(path)

        metadata = {
            "path": path,
            "full_path": full_path,
            "exists": os.path.isfile(full_path),
        }

        if metadata["exists"]:
            stat = os.stat(full_path)
            metadata["size"] = stat.st_size
            metadata["created_at"] = int(stat.st_ctime * 1000)
            metadata["modified_at"] = int(stat.st_mtime * 1000)

        return metadata

    def cleanup_empty_dirs(self, path: str):
        """Remove empty directories up to base path."""
        full_path = self._full_path(path)
        dir_path = os.path.dirname(full_path)

        while dir_path != self._base_path and dir_path.startswith(self._base_path):
            try:
                if os.path.isdir(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    dir_path = os.path.dirname(dir_path)
                else:
                    break
            except OSError:
                break
