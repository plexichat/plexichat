"""
Hashing operations mixin for DeduplicationManager.
"""

import hashlib
from typing import Any, Dict, Optional

import utils.logger as logger


class HashOperationsMixin:
    """Provides hashing operations including SHA-256 and perceptual hashing."""

    __slots__ = ()

    _db: Any
    _config: dict[str, Any]

    def compute_hash(self, file_data: bytes) -> str:
        """Compute hash of file data."""
        algorithm = self._config["hash_algorithm"]

        if algorithm == "sha512":
            return hashlib.sha512(file_data).hexdigest()
        elif algorithm == "blake2b":
            return hashlib.blake2b(file_data).hexdigest()
        else:
            return hashlib.sha256(file_data).hexdigest()

    def compute_phash(self, file_data: bytes, content_type: str) -> Optional[str]:
        """Compute perceptual hash for images."""
        if not self._config.get("phash_enabled", True):
            return None

        if not content_type.lower().startswith("image/"):
            return None

        try:
            from src.core.media.phash import compute_phash, is_available

            if not is_available():
                return None
            return compute_phash(file_data)
        except Exception as e:
            logger.warning(f"Failed to compute pHash: {e}")
            return None

    def register_or_update_phash(
        self,
        hash_value: str,
        phash_value: str,
        file_size: int,
        content_type: str,
        timestamp: int,
    ) -> None:
        """Register a new hash record or update pHash on an existing one."""
        if not self._config["enabled"]:
            return

        row = self._db.fetch_one(
            "SELECT id, reference_count FROM media_file_hashes WHERE hash_value = ?",
            (hash_value,),
        )

        if row:
            hash_id = row["id"] if isinstance(row, dict) else row[0]
            self._db.execute(
                "UPDATE media_file_hashes SET phash_value = ?, reference_count = reference_count + 1 WHERE id = ?",
                (phash_value, hash_id),
            )
        else:
            from src.utils.encryption import generate_snowflake_id

            hash_id = generate_snowflake_id()
            self._db.execute(
                """INSERT INTO media_file_hashes
                   (id, hash_value, phash_value, algorithm, file_size, content_type,
                    reference_count, first_seen, storage_path, storage_backend)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, '', '')""",
                (
                    hash_id,
                    hash_value,
                    phash_value,
                    self._config["hash_algorithm"],
                    file_size,
                    content_type,
                    timestamp,
                ),
            )

    def _find_similar_by_phash(self, phash_value: str) -> Optional[Dict[str, Any]]:
        """Find existing file with similar pHash."""
        if not self._config.get("phash_enabled", True):
            return None

        threshold = self._config.get("phash_threshold", 10)

        rows = self._db.fetch_all(
            "SELECT id, phash_value, storage_path FROM media_file_hashes WHERE phash_value IS NOT NULL"
        )

        try:
            from src.core.media.phash import hamming_distance

            for row in rows:
                if isinstance(row, dict):
                    stored_phash = row["phash_value"]
                    file_id = row["id"]
                    storage_path = row["storage_path"]
                else:
                    file_id, stored_phash, storage_path = row

                if stored_phash:
                    distance = hamming_distance(phash_value, stored_phash)
                    if 0 <= distance <= threshold:
                        return {"id": file_id, "storage_path": storage_path}
        except Exception as e:
            logger.warning(f"pHash similarity search failed: {e}")

        return None
