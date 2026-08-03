"""Low-level blob storage through the configured storage adapter.

Suite domains such as PlexiSum store their workbook files as independent
SQLite databases through this boundary. Blobs go to the same S3/local storage
backend the media module uses, but they bypass the media *upload pipeline*
(content-type whitelist, scanning, thumbnailing) because they are
server-authored data files, not user media uploads.

Blob keys are opaque and namespaced under ``plexisum/``. Callers must treat
paths as opaque identifiers and never accept a client-supplied path.
"""

from typing import Any

import utils.logger as logger

BLOB_NAMESPACE = "plexisum"
BLOB_CONTENT_TYPE = "application/x-sqlite3"


class _BlobMixin:
    """Storage-adapter blob helpers for server-authored workbook files."""

    # Set by the concrete media manager; declared here so pyright can
    # resolve the storage-adapter calls in this mixin's methods.
    _storage: Any = None

    def store_blob(
        self,
        data: bytes,
        key: str,
        content_type: str = BLOB_CONTENT_TYPE,
    ) -> str:
        """Store raw bytes under ``plexisum/{key}`` and return the opaque path."""
        safe_key = self._sanitize_blob_key(key)
        path = f"{BLOB_NAMESPACE}/{safe_key}"
        self._storage.store(data, path, content_type)
        logger.debug("Stored blob at %s (%d bytes)", path, len(data))
        return path

    def retrieve_blob(self, path: str) -> bytes:
        """Retrieve raw bytes for a blob path previously returned by store_blob."""
        if not isinstance(path, str) or not path.startswith(f"{BLOB_NAMESPACE}/"):
            raise ValueError("Invalid blob path")
        return self._storage.retrieve(path)

    def delete_blob(self, path: str) -> bool:
        """Delete a blob path. Returns True when the object was removed."""
        if not isinstance(path, str) or not path.startswith(f"{BLOB_NAMESPACE}/"):
            raise ValueError("Invalid blob path")
        try:
            return bool(self._storage.delete(path))
        except Exception:
            logger.warning("Blob delete failed for %s", path, exc_info=True)
            return False

    def get_blob_size(self, path: str) -> int:
        """Return the stored blob size in bytes (0 when unknown)."""
        try:
            return int(self._storage.get_size(path))
        except Exception:
            return 0

    @staticmethod
    def _sanitize_blob_key(key: str) -> str:
        """Keep blob keys safe for use inside storage paths."""
        value = str(key or "")
        # Allowed: alphanumerics, dash, underscore, dot, slash separators for
        # revision namespacing. Reject anything that could traverse or escape.
        if (
            not value
            or len(value) > 256
            or value.startswith((".", "/", "\\"))
            or ".." in value
            or any(char in value for char in ("\x00", "\r", "\n"))
        ):
            raise ValueError("Invalid blob key")
        return value
