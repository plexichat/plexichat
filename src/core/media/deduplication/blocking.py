"""
Blocking operations mixin for DeduplicationManager.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import utils.logger as logger


class BlockingMixin:
    """Provides blocking operations for hashes and users."""

    __slots__ = ()

    _db: Any
    _config: dict[str, Any]

    def is_blocked(
        self, hash_value: str, phash_value: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """Check if a hash is blocked (by SHA256 or pHash similarity)."""
        row = self._db.fetch_one(
            "SELECT reason FROM media_blocked_hashes WHERE hash_value = ? AND hash_type = 'sha256'",
            (hash_value,),
        )
        if row:
            reason = row["reason"] if isinstance(row, dict) else row[0]
            return True, reason

        if phash_value and self._config.get("phash_enabled", True):
            blocked_phashes = self._db.fetch_all(
                "SELECT hash_value, phash_threshold, reason FROM media_blocked_hashes WHERE hash_type = 'phash'"
            )

            try:
                from src.core.media.phash import hamming_distance

                for row in blocked_phashes:
                    if isinstance(row, dict):
                        blocked_hash = row["hash_value"]
                        threshold = row.get("phash_threshold", 10)
                        reason = row["reason"]
                    else:
                        blocked_hash, threshold, reason = row[0], row[1] or 10, row[2]

                    distance = hamming_distance(phash_value, blocked_hash)
                    if 0 <= distance <= threshold:
                        return True, f"{reason} (similar image detected)"
            except Exception as e:
                logger.warning(f"pHash comparison failed: {e}")

        return False, None

    def is_user_blocked(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """Check if a user is blocked from uploading."""
        now = int(time.time() * 1000)

        row = self._db.fetch_one(
            "SELECT reason, expires_at FROM media_blocked_users WHERE user_id = ?",
            (user_id,),
        )
        if row:
            if isinstance(row, dict):
                reason = row["reason"]
                expires_at = row.get("expires_at")
            else:
                reason, expires_at = row[0], row[1]

            if expires_at and expires_at < now:
                self._db.execute(
                    "DELETE FROM media_blocked_users WHERE user_id = ?", (user_id,)
                )
                return False, None

            return True, reason
        return False, None

    def block_hash(
        self,
        hash_value: str,
        reason: str,
        blocked_by: Optional[int] = None,
        auto: bool = False,
        hash_type: str = "sha256",
        phash_threshold: int = 10,
    ) -> bool:
        """Block a hash from being uploaded."""
        now = int(time.time() * 1000)

        try:
            self._db.upsert(
                "media_blocked_hashes",
                [
                    "hash_value",
                    "hash_type",
                    "phash_threshold",
                    "reason",
                    "blocked_at",
                    "blocked_by",
                    "auto_blocked",
                ],
                (
                    hash_value,
                    hash_type,
                    phash_threshold,
                    reason,
                    now,
                    blocked_by,
                    1 if auto else 0,
                ),
                conflict_columns=["hash_value"],
            )

            self._db.execute(
                """UPDATE media_hash_reports
                   SET status = 'blocked', reviewed_at = ?, reviewed_by = ?
                   WHERE hash_value = ? AND status = 'pending'""",
                (now, blocked_by, hash_value),
            )

            logger.info(
                f"Hash {hash_value[:16]}... blocked (type={hash_type}): {reason}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to block hash: {e}")
            return False

    def unblock_hash(self, hash_value: str) -> bool:
        """Unblock a hash."""
        try:
            self._db.execute(
                "DELETE FROM media_blocked_hashes WHERE hash_value = ?", (hash_value,)
            )
            logger.info(f"Hash {hash_value[:16]}... unblocked")
            return True
        except Exception as e:
            logger.error(f"Failed to unblock hash: {e}")
            return False

    def block_user(
        self,
        user_id: int,
        reason: str,
        blocked_by: Optional[int] = None,
        duration_hours: Optional[int] = None,
    ) -> bool:
        """Block a user from uploading media."""
        now = int(time.time() * 1000)
        expires_at = None
        if duration_hours:
            expires_at = now + (duration_hours * 3600 * 1000)

        try:
            self._db.upsert(
                "media_blocked_users",
                ["user_id", "reason", "blocked_at", "blocked_by", "expires_at"],
                (user_id, reason, now, blocked_by, expires_at),
                conflict_columns=["user_id"],
            )
            logger.info(f"User {user_id} blocked from uploads: {reason}")
            return True
        except Exception as e:
            logger.error(f"Failed to block user: {e}")
            return False

    def unblock_user(self, user_id: int) -> bool:
        """Unblock a user from uploading media."""
        try:
            self._db.execute(
                "DELETE FROM media_blocked_users WHERE user_id = ?", (user_id,)
            )
            logger.info(f"User {user_id} unblocked from uploads")
            return True
        except Exception as e:
            logger.error(f"Failed to unblock user: {e}")
            return False

    def get_blocked_users(
        self, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get list of blocked users."""
        rows = self._db.fetch_all(
            """SELECT user_id, reason, blocked_at, blocked_by, expires_at
               FROM media_blocked_users
               ORDER BY blocked_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )

        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(
                    {
                        "user_id": row["user_id"],
                        "reason": row["reason"],
                        "blocked_at": row["blocked_at"],
                        "blocked_by": row["blocked_by"],
                        "expires_at": row.get("expires_at"),
                    }
                )
            else:
                result.append(
                    {
                        "user_id": row[0],
                        "reason": row[1],
                        "blocked_at": row[2],
                        "blocked_by": row[3],
                        "expires_at": row[4],
                    }
                )

        return result

    def get_blocked_hashes(
        self, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get list of blocked hashes."""
        rows = self._db.fetch_all(
            """SELECT hash_value, reason, blocked_at, blocked_by, auto_blocked
               FROM media_blocked_hashes
               ORDER BY blocked_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )

        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(
                    {
                        "hash_value": row["hash_value"],
                        "reason": row["reason"],
                        "blocked_at": row["blocked_at"],
                        "blocked_by": row["blocked_by"],
                        "auto_blocked": bool(row["auto_blocked"]),
                    }
                )
            else:
                result.append(
                    {
                        "hash_value": row[0],
                        "reason": row[1],
                        "blocked_at": row[2],
                        "blocked_by": row[3],
                        "auto_blocked": bool(row[4]),
                    }
                )

        return result
