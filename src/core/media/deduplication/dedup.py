"""
Deduplication operations mixin for DeduplicationManager.
"""

from typing import Any, Dict, Optional

from .constants import DeduplicationResult


class DeduplicationMixin:
    _db: Any
    _config: Dict[str, Any]

    def check_duplicate(
        self, file_data: bytes, content_type: str, user_id: Optional[int] = None
    ) -> DeduplicationResult:
        """Check if file is a duplicate and if it's blocked."""
        hash_value = self.compute_hash(file_data)
        phash_value = self.compute_phash(file_data, content_type)

        if not self._config["enabled"]:
            return DeduplicationResult(is_duplicate=False, hash_value=hash_value)

        if user_id:
            user_blocked, user_reason = self.is_user_blocked(user_id)
            if user_blocked:
                return DeduplicationResult(
                    is_duplicate=False,
                    hash_value=hash_value,
                    is_blocked=True,
                    block_reason=f"User blocked: {user_reason}",
                )

        file_size = len(file_data)

        if file_size < self._config["min_size"]:
            return DeduplicationResult(is_duplicate=False, hash_value=hash_value)

        is_blocked, block_reason = self.is_blocked(hash_value, phash_value)
        if is_blocked:
            return DeduplicationResult(
                is_duplicate=False,
                hash_value=hash_value,
                is_blocked=True,
                block_reason=block_reason,
            )

        row = self._db.fetch_one(
            """SELECT id, storage_path, storage_backend
               FROM media_file_hashes WHERE hash_value = ?""",
            (hash_value,),
        )

        if row:
            if isinstance(row, dict):
                file_id = row["id"]
                storage_path = row["storage_path"]
            else:
                file_id, storage_path, _ = row

            return DeduplicationResult(
                is_duplicate=True,
                hash_value=hash_value,
                existing_file_id=file_id,
                existing_url=storage_path,
            )

        if phash_value:
            similar = self._find_similar_by_phash(phash_value)
            if similar:
                return DeduplicationResult(
                    is_duplicate=True,
                    hash_value=hash_value,
                    existing_file_id=similar["id"],
                    existing_url=similar["storage_path"],
                )

        return DeduplicationResult(is_duplicate=False, hash_value=hash_value)

    def check_duplicate_by_hash(
        self,
        hash_value: str,
        content_type: str,
        file_size: int,
        user_id: Optional[int] = None,
        phash_value: Optional[str] = None,
    ) -> DeduplicationResult:
        if not self._config["enabled"]:
            return DeduplicationResult(is_duplicate=False, hash_value=hash_value)

        if user_id:
            user_blocked, user_reason = self.is_user_blocked(user_id)
            if user_blocked:
                return DeduplicationResult(
                    is_duplicate=False,
                    hash_value=hash_value,
                    is_blocked=True,
                    block_reason=f"User blocked: {user_reason}",
                )

        if file_size < self._config["min_size"]:
            return DeduplicationResult(is_duplicate=False, hash_value=hash_value)

        is_blocked, block_reason = self.is_blocked(hash_value, phash_value)
        if is_blocked:
            return DeduplicationResult(
                is_duplicate=False,
                hash_value=hash_value,
                is_blocked=True,
                block_reason=block_reason,
            )

        row = self._db.fetch_one(
            """SELECT id, storage_path, storage_backend
               FROM media_file_hashes WHERE hash_value = ?""",
            (hash_value,),
        )

        if row:
            if isinstance(row, dict):
                file_id = row["id"]
                storage_path = row["storage_path"]
            else:
                file_id, storage_path, _ = row

            return DeduplicationResult(
                is_duplicate=True,
                hash_value=hash_value,
                existing_file_id=file_id,
                existing_url=storage_path,
            )

        if phash_value:
            similar = self._find_similar_by_phash(phash_value)
            if similar:
                return DeduplicationResult(
                    is_duplicate=True,
                    hash_value=hash_value,
                    existing_file_id=similar["id"],
                    existing_url=similar["storage_path"],
                )

        return DeduplicationResult(is_duplicate=False, hash_value=hash_value)

    def _check_exact_hash(self, hash_value: str) -> Optional[DeduplicationResult]:
        """Fast exact-hash duplicate check (no pHash scan). 1 DB query."""
        row = self._db.fetch_one(
            """SELECT id, storage_path, storage_backend
               FROM media_file_hashes WHERE hash_value = ?""",
            (hash_value,),
        )
        if row:
            if isinstance(row, dict):
                file_id = row["id"]
                storage_path = row["storage_path"]
            else:
                file_id, storage_path, _ = row
            return DeduplicationResult(
                is_duplicate=True,
                hash_value=hash_value,
                existing_file_id=file_id,
                existing_url=storage_path,
            )
        return None

    def register_file(
        self,
        hash_value: str,
        file_size: int,
        content_type: str,
        storage_path: str,
        storage_backend: str,
        timestamp: int,
        phash_value: Optional[str] = None,
    ) -> int:
        """Register a new file hash or increment reference count."""
        if not self._config["enabled"]:
            return 0

        row = self._db.fetch_one(
            "SELECT id, reference_count FROM media_file_hashes WHERE hash_value = ?",
            (hash_value,),
        )

        if row:
            hash_id = row["id"] if isinstance(row, dict) else row[0]
            self._db.execute(
                "UPDATE media_file_hashes SET reference_count = reference_count + 1 WHERE id = ?",
                (hash_id,),
            )
            return hash_id

        from src.utils.encryption import generate_snowflake_id

        hash_id = generate_snowflake_id()

        self._db.execute(
            """INSERT INTO media_file_hashes
               (id, hash_value, phash_value, algorithm, file_size, content_type, reference_count,
                first_seen, storage_path, storage_backend)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                hash_id,
                hash_value,
                phash_value,
                self._config["hash_algorithm"],
                file_size,
                content_type,
                timestamp,
                storage_path,
                storage_backend,
            ),
        )

        return hash_id

    def decrement_reference(self, hash_value: str) -> bool:
        """Decrement reference count for a hash."""
        if not self._config["enabled"]:
            return True

        row = self._db.fetch_one(
            "SELECT id, reference_count FROM media_file_hashes WHERE hash_value = ?",
            (hash_value,),
        )

        if not row:
            return True

        ref_count = row["reference_count"] if isinstance(row, dict) else row[1]
        hash_id = row["id"] if isinstance(row, dict) else row[0]

        if ref_count <= 1:
            self._db.execute("DELETE FROM media_file_hashes WHERE id = ?", (hash_id,))
            return True
        else:
            self._db.execute(
                "UPDATE media_file_hashes SET reference_count = reference_count - 1 WHERE id = ?",
                (hash_id,),
            )
            return False
