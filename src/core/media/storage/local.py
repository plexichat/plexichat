"""
Local filesystem storage backend.
"""

import os
import stat
from typing import BinaryIO, Tuple

import utils.logger as logger

from src.core.security.hardening import HardeningViolation, safe_join

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
        # SECURITY: compare against the base path plus a separator (and resolve
        # symlinks) so sibling directories such as ``<base>-evil/`` or symlink
        # escapes cannot pass a naive ``startswith`` check.
        try:
            safe_join(self._base_path, clean_path)
        except HardeningViolation as exc:
            raise StorageError(
                "Invalid path: path traversal detected", "local"
            ) from exc
        return full

    def _ensure_dir(self, file_path: str):
        """Ensure directory exists for file."""
        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

    def _reject_symlink_components(self, path: str) -> None:
        """Refuse to write through any symlink already present in ``path``.

        ``_full_path`` validates containment by *resolving* symlinks, but the
        write itself targets the original (unresolved) path. Without this
        check an in-base symlink silently redirects the write to a different
        object, and a symlink planted between the containment check and the
        ``open`` (TOCTOU) can escape the base entirely. Every existing path
        component is therefore inspected with ``lstat`` (which never follows
        the link); the final write additionally uses ``O_NOFOLLOW``.
        """
        clean = path.lstrip("/").lstrip("\\")
        current = self._base_path
        for part in clean.split("/"):
            if not part or part == ".":
                continue
            current = os.path.join(current, part)
            try:
                mode = os.lstat(current).st_mode
            except (FileNotFoundError, NotADirectoryError):
                # Not created yet - nothing to hijack at this component.
                break
            if stat.S_ISLNK(mode):
                raise StorageError(
                    "Invalid path: symlink component detected", "local"
                )

    @staticmethod
    def _open_for_write(full_path: str) -> int:
        """Open ``full_path`` for writing without following a final symlink.

        ``O_NOFOLLOW`` fails with ``ELOOP`` if the destination itself is a
        symlink, closing the check-then-open race on the last component while
        preserving overwrite (``O_TRUNC``) semantics for regular files.
        """
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        return os.open(full_path, flags, 0o600)

    def store(self, file_data: bytes, path: str, content_type: str) -> str:
        """Store file data at the specified path."""
        full_path = self._full_path(path)

        try:
            self._reject_symlink_components(path)
            self._ensure_dir(full_path)
            # Re-check after mkdir: a directory component planted as a symlink
            # during directory creation must not survive into the write.
            self._reject_symlink_components(path)
            fd = self._open_for_write(full_path)
            with os.fdopen(fd, "wb") as f:
                f.write(file_data)
            logger.debug(f"Stored file at {full_path} ({len(file_data)} bytes)")
            return path
        except StorageError:
            raise
        except OSError as e:
            logger.error(f"Failed to store file at {full_path}: {e}")
            raise StorageWriteError(f"Failed to write file: {e}", "local")

    def store_stream(
        self, stream: BinaryIO, path: str, content_type: str, size: int
    ) -> str:
        """Store file from a stream."""
        full_path = self._full_path(path)

        try:
            self._reject_symlink_components(path)
            self._ensure_dir(full_path)
            self._reject_symlink_components(path)
            fd = self._open_for_write(full_path)
            bytes_written = 0
            with os.fdopen(fd, "wb") as f:
                chunk_size = 8192
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_written += len(chunk)
            logger.debug(f"Stored stream at {full_path} ({bytes_written} bytes)")
            return path
        except StorageError:
            raise
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

    def is_encrypted(self, path: str) -> bool:
        """Check if file is encrypted."""
        return False

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
