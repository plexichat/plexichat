"""
Export format generators for DSAR (Data Subject Access Request) module.

Generates JSON and ZIP archives of user data, then writes the result to the
configured storage backend (reusing the media module's storage backends: local
filesystem, S3, or database BLOB).

Encryption is handled at the storage layer (via `wrap_storage_with_encryption`)
when `media.encrypt_at_rest` is enabled; DSAR does not add its own envelope
encryption on top to avoid double-encrypting and to behave like every other
uploaded file in the system.
"""

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Tuple

import utils.config as config
import utils.logger as logger

from src.core.media.storage import (
    LocalStorage,
    S3Storage,
    DatabaseStorage,
    StorageBackendBase,
)


class ExportFormatGenerator:
    """
    Generates DSAR exports in various formats (JSON, ZIP) and stores them using
    the same storage backend(s) the media module uses for uploaded files.
    """

    def __init__(self, db=None, config_override: Optional[Dict[str, Any]] = None):
        self._db = db
        self._dsar_config = config_override or config.get("dsar", {}) or {}
        self._media_config = config.get("media", {}) or {}
        self._backend = self._build_storage()

    def _build_storage(self) -> StorageBackendBase:
        """
        Build the storage backend instance for export files.

        Reuses the `media` config block for backend selection and credentials
        so DSAR exports go through the same storage layer as uploaded files.
        `dsar.local_path` / `dsar.s3_path_prefix` override the defaults if set.
        """
        backend_name = self._media_config.get("storage_backend", "local")

        if backend_name == "s3":
            storage = S3Storage(
                bucket=self._media_config.get("s3_bucket", "plexichat-media"),
                access_key=self._media_config.get("s3_access_key", ""),
                secret_key=self._media_config.get("s3_secret_key", ""),
                region=self._media_config.get("s3_region", "us-east-1"),
                endpoint_url=self._media_config.get("s3_endpoint") or None,
                public_url=self._media_config.get("s3_public_url") or None,
                path_prefix=self._dsar_config.get("s3_path_prefix", "dsar-exports"),
            )
        elif backend_name == "database":
            if self._db is None:
                raise RuntimeError(
                    "Database storage backend requires a Database instance"
                )
            storage = DatabaseStorage(
                db=self._db,
                base_url=self._media_config.get("database_url", "/api/v1/media/blob"),
                max_size=self._media_config.get("database_max_size", 524288),
            )
        else:
            default_local = str(
                Path.home() / ".plexichat" / "data" / "exports" / "dsar"
            )
            storage = LocalStorage(
                base_path=self._dsar_config.get("local_path", default_local),
                base_url=self._media_config.get("local_url", "/media"),
            )

        return storage

    def _build_storage_path(self, request_id: int, extension: str) -> str:
        """
        Build a relative storage path for an export file.

        The path is intentionally similar to media storage paths so it can be
        served through the same download pipeline.
        """
        date_dir = time.strftime("%Y/%m")
        return f"dsar/{date_dir}/dsar_{request_id}.{extension}"

    def _store(
        self,
        data: bytes,
        request_id: int,
        extension: str,
        content_type: str,
    ) -> Tuple[str, int]:
        """
        Write `data` to the configured storage backend.

        Returns (storage_path, file_size_bytes).
        """
        path = self._build_storage_path(request_id, extension)
        returned_path = self._backend.store(data, path, content_type)
        size = self._backend.get_size(returned_path)
        return returned_path, size

    def _checksum(self, data: bytes) -> str:
        import hashlib

        return hashlib.sha256(data).hexdigest()

    def generate_json(
        self, data: Dict, request_id: int, user_id: int
    ) -> Tuple[str, str, int]:
        """
        Generate a JSON export and store it via the configured backend.

        Returns (storage_path, checksum, file_size).
        """
        envelope = {
            "export_type": "dsar_json",
            "export_version": "1.0",
            "request_id": request_id,
            "user_id": user_id,
            "generated_at": int(time.time()),
            "data": data,
        }

        json_content = json.dumps(envelope, indent=2, default=str).encode("utf-8")
        checksum = self._checksum(json_content)
        content_type = "application/json"

        storage_path, file_size = self._store(
            json_content, request_id, "json", content_type
        )

        logger.info(
            f"Generated JSON export for request {request_id}: "
            f"{file_size} bytes (backend={self.backend_name}), "
            f"checksum {checksum[:16]}..."
        )

        return storage_path, checksum, file_size

    def generate_zip(
        self, data: Dict, request_id: int, user_id: int
    ) -> Tuple[str, str, int]:
        """
        Generate a ZIP export with separate JSON files per category and store
        it via the configured backend.

        Returns (storage_path, checksum, file_size).
        """
        buffer = io.BytesIO()
        manifest = {
            "export_type": "dsar_zip",
            "export_version": "1.0",
            "request_id": request_id,
            "user_id": user_id,
            "generated_at": int(time.time()),
            "categories": list(data.keys()),
        }

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, default=str).encode("utf-8"),
            )
            for category, category_data in data.items():
                zf.writestr(
                    f"{category}.json",
                    json.dumps(category_data, indent=2, default=str).encode("utf-8"),
                )

        zip_bytes = buffer.getvalue()
        checksum = self._checksum(zip_bytes)
        content_type = "application/zip"

        storage_path, file_size = self._store(
            zip_bytes, request_id, "zip", content_type
        )

        logger.info(
            f"Generated ZIP export for request {request_id}: "
            f"{file_size} bytes (backend={self.backend_name}), "
            f"checksum {checksum[:16]}..."
        )

        return storage_path, checksum, file_size

    def retrieve(self, storage_path: str) -> bytes:
        """Retrieve a previously generated export."""
        return self._backend.retrieve(storage_path)

    def retrieve_stream(self, storage_path: str) -> Tuple[BinaryIO, int]:
        """Retrieve a previously generated export as a stream."""
        return self._backend.retrieve_stream(storage_path)

    def delete(self, storage_path: str) -> bool:
        """Delete a generated export (used on expiry/cancellation)."""
        try:
            return self._backend.delete(storage_path)
        except Exception as e:
            logger.warning(f"Failed to delete DSAR export at {storage_path}: {e}")
            return False

    @property
    def backend_name(self) -> str:
        return self._media_config.get("storage_backend", "local")
