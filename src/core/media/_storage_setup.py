# pyright: reportAttributeAccessIssue=false
"""
Storage-backend setup helpers mixed into MediaManager.
"""

import os
import hashlib
import base64
import uuid
import time
import json
import logging
from typing import Optional, Dict, Any, Tuple

import utils.config as config

from .models import MediaType, StorageBackend, MediaFile, ScanStatus
from .storage import (
    LocalStorage,
    S3Storage,
    DatabaseStorage,
    StorageBackendBase,
    wrap_storage_with_encryption,
)
from ._config import DEFAULT_SIZE_LIMITS, DEFAULT_ALLOWED_TYPES, DEFAULT_THUMBNAIL_SIZES

logger = logging.getLogger(__name__)


class _StorageSetupMixin:
    """Storage initialisation and helpers mixed into MediaManager."""

    # -- config loading ----------------------------------------------------------

    def _load_config(self) -> Dict[str, Any]:
        """Load media configuration from global config."""
        cfg = config.get("media")
        if cfg is None:
            cfg = config.get("storage", {})
        if not isinstance(cfg, dict):
            cfg = {}
        if "size_limits" not in cfg:
            cfg["size_limits"] = DEFAULT_SIZE_LIMITS.copy()
        if "allowed_types" not in cfg:
            cfg["allowed_types"] = DEFAULT_ALLOWED_TYPES.copy()
        if "rate_limit" not in cfg:
            cfg["rate_limit"] = {"enabled": True}
        if "image_processing" not in cfg:
            cfg["image_processing"] = {
                "max_thumbnail_requests_per_minute": 60,
                "thumbnail_sizes": DEFAULT_THUMBNAIL_SIZES,
            }
        if "auto_route_to_database" not in cfg:
            cfg["auto_route_to_database"] = {"enabled": False}
        return cfg

    # -- primary storage ---------------------------------------------------------

    def _init_storage(self) -> StorageBackendBase:
        """Initialize primary storage backend."""
        backend = self._config.get("storage_backend", "local")
        encrypt_at_rest = self._config.get("encrypt_at_rest", True)

        if backend == "s3":
            storage = S3Storage(
                bucket=self._config.get("s3_bucket", "plexichat-media"),
                access_key=self._config.get("s3_access_key", ""),
                secret_key=self._config.get("s3_secret_key", ""),
                region=self._config.get("s3_region", "us-east-1"),
                endpoint_url=self._config.get("s3_endpoint") or None,
                public_url=self._config.get("s3_public_url") or None,
            )
        elif backend == "database":
            storage = DatabaseStorage(
                db=self._db,
                base_url=self._config.get("database_url", "/api/v1/media/blob"),
                max_size=self._config.get("database_max_size", 512 * 1024),
            )
        else:
            storage = LocalStorage(
                base_path=self._config.get("local_path", "uploads"),
                base_url=self._config.get("local_url", "/media"),
            )

        if encrypt_at_rest:
            signing_key = self._config.get("signing_key")
            if signing_key and signing_key not in [
                "",
                "CHANGE_THIS_SIGNING_KEY",
                "change-me",
                "changeme",
            ]:
                auth_config = config.get("authentication", {})
                media_key = auth_config.get("encryption", {}).get(
                    "media_key"
                ) or os.environ.get("PLEXICHAT_MEDIA_KEY")
                if not media_key:
                    derived_key = hashlib.sha256(signing_key.encode()).digest()
                    os.environ["PLEXICHAT_MEDIA_KEY"] = base64.b64encode(
                        derived_key
                    ).decode()
                    logger.info(
                        "Derived media encryption key from signing key "
                        "(no PLEXICHAT_MEDIA_KEY env var set externally, "
                        "injected into process environment)"
                    )
                storage = wrap_storage_with_encryption(storage, enabled=True)
                logger.info(f"File encryption at rest enabled for {backend} storage")
        return storage

    # -- DB storage (auto-routing) -----------------------------------------------

    def _init_db_storage(self) -> Optional[StorageBackendBase]:
        """Initialize database storage for auto-routing (if enabled and not primary)."""
        auto_route = self._config.get("auto_route_to_database", {})
        primary_backend = self._config.get("storage_backend", "local")
        encrypt_at_rest = self._config.get("encrypt_at_rest", True)

        if auto_route.get("enabled", False) and primary_backend != "database":
            storage = DatabaseStorage(
                db=self._db,
                base_url=self._config.get("database_url", "/api/v1/media/blob"),
                max_size=auto_route.get("max_size", 512 * 1024),
            )
            if encrypt_at_rest:
                storage = wrap_storage_with_encryption(storage, enabled=True)
                logger.debug("Encrypted database storage initialized for auto-routing")
            return storage
        return None

    # -- storage routing ---------------------------------------------------------

    def _get_storage_for_file(
        self, content_type: str, size: int
    ) -> Tuple[StorageBackendBase, str]:
        """Determine the correct storage backend for a file."""
        auto_route = self._config.get("auto_route_to_database", {})
        if (
            auto_route.get("enabled", False)
            and self._db_storage
            and size <= auto_route.get("max_size", 512 * 1024)
        ):
            allowed_types = auto_route.get(
                "allowed_types",
                ["text/plain", "application/json", "application/javascript"],
            )
            if content_type in allowed_types or "*" in allowed_types:
                return self._db_storage, "database"
        return self._storage, self._config.get("storage_backend", "local")

    def _should_route_to_database(self, content_type: str, size: int) -> bool:
        auto_route = self._config.get("auto_route_to_database", {})
        if not auto_route.get("enabled", False):
            return False
        if not self._db_storage:
            return False
        if size > auto_route.get("max_size", 512 * 1024):
            return False
        allowed_types = auto_route.get(
            "allowed_types",
            ["text/plain", "application/json", "application/javascript"],
        )
        return content_type in allowed_types or "*" in allowed_types

    def _get_storage_by_backend(self, backend: str) -> StorageBackendBase:
        """Get storage instance for a specific backend type."""
        if backend == "database":
            return self._db_storage if self._db_storage else self._storage
        elif backend == self._config.get("storage_backend", "local"):
            return self._storage
        else:
            logger.warning(f"File backend '{backend}' differs from current config")
            return self._storage

    # -- path / checksum helpers -------------------------------------------------

    def _generate_storage_path(self, filename: str, media_type: MediaType) -> str:
        ext = os.path.splitext(filename)[1].lower() or ".bin"
        unique_id = uuid.uuid4().hex[:16]
        type_dir = media_type.value
        date_dir = time.strftime("%Y/%m/%d")
        return f"{type_dir}/{date_dir}/{unique_id}{ext}"

    def _compute_checksum(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    # -- row conversion ----------------------------------------------------------

    def _row_to_media_file(self, row) -> MediaFile:
        metadata = None
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse media metadata for {row['id']}: {e}")
        backend = row["storage_backend"]
        storage = self._get_storage_by_backend(backend)
        return MediaFile(
            id=row["id"],
            filename=row["filename"],
            original_filename=row["original_filename"],
            content_type=row["content_type"],
            size=row["size"],
            media_type=MediaType(row["media_type"]),
            storage_backend=StorageBackend(backend),
            storage_path=row["storage_path"],
            url=storage.get_url(row["storage_path"]),
            checksum=row["checksum"],
            uploaded_by=row["uploaded_by"],
            uploaded_at=row["uploaded_at"],
            metadata=metadata,
            scan_status=ScanStatus(row["scan_status"]),
            scan_result=row["scan_result"],
            deleted=bool(row["deleted"]),
            deleted_at=row["deleted_at"],
        )
