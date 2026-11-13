"""Central storage-quota policy for Plexichat and PlexiSuite uploads.

Resolution order (highest priority first):

1. A per-user administrator override (``user_storage_quotas``).
2. The user's rate-limit tier ``max_storage_mb`` (``-1`` = unlimited).
3. The global ``storage.default_quota_mb`` config value.
4. A conservative built-in fallback.

Usage is the sum of live ``media_files`` rows uploaded by the user. That covers
chat attachments, avatars, emojis, stickers, sounds, and every PlexiSuite Stash
upload because they all pass through the media manager. Deduplicated physical
blobs are intentionally still charged per owning media row: the quota is about
what a user may keep, not how many bytes the storage backend happens to share.

The reservation ledger closes the check-then-upload race. A reservation is
inserted with a short TTL before bytes are stored and removed in a ``finally``;
concurrent uploads see each other's pending bytes.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

import utils.config as config
import utils.logger as logger

from src.core.media.exceptions import StorageQuotaExceededError

from .schema import ensure_schema

MIB = 1024 * 1024
DEFAULT_QUOTA_MB = 5 * 1024
DEFAULT_RESERVATION_TTL_SECONDS = 900
_UNLIMITED_MB = -1


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[index]
    except (TypeError, IndexError, KeyError):
        return None


class StorageQuotaManager:
    """Resolve, report, and enforce per-user storage limits."""

    def __init__(self, db, config_override: Optional[dict] = None):
        self._db = db
        self._config_override = config_override or {}
        self._schema_ready = False

    # ── config / policy -----------------------------------------------------

    def _storage_config(self) -> dict:
        if isinstance(self._config_override, dict) and self._config_override:
            return self._config_override
        try:
            value = config.get("storage", {})
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _default_limit_bytes(self) -> int:
        value = self._storage_config().get("default_quota_mb", DEFAULT_QUOTA_MB)
        mb = _as_int(value, DEFAULT_QUOTA_MB)
        if mb < 0:
            return -1
        return mb * MIB

    def _reservation_ttl_seconds(self) -> int:
        value = self._storage_config().get(
            "reservation_ttl_seconds", DEFAULT_RESERVATION_TTL_SECONDS
        )
        return max(60, min(3600, _as_int(value, DEFAULT_RESERVATION_TTL_SECONDS)))

    def _user_tier(self, user_id: int) -> str:
        try:
            from src.core import features

            if features.is_setup():
                return str(features.get_user_tier(int(user_id)) or "standard")
        except Exception as tier_error:
            # Fall back to the configured default tier below.
            logger.debug(f"user tier lookup failed: {tier_error}")
        try:
            value = config.get("user_features", {}).get("default_tier", "standard")
            return str(value or "standard")
        except Exception:
            return "standard"

    def _tier_limit_bytes(self, tier: str) -> Optional[int]:
        try:
            from src.core import features

            limits = features.get_tier_limits(tier)
            mb = _as_int(getattr(limits, "max_storage_mb", None), 0)
            if mb == _UNLIMITED_MB:
                return -1
            if mb > 0:
                return mb * MIB
        except Exception as limit_error:
            logger.debug(f"quota limit lookup failed: {limit_error}")
        return None

    def _override_row(self, user_id: int) -> Optional[Any]:
        self._ensure_schema()
        return self._db.fetch_one(
            "SELECT user_id, limit_bytes, unlimited, note, updated_by, updated_at "
            "FROM user_storage_quotas WHERE user_id = ?",
            (int(user_id),),
        )

    def resolve_limit(self, user_id: int) -> Dict[str, Any]:
        """Return the effective limit and where it came from."""
        tier = self._user_tier(user_id)
        override = self._override_row(user_id)
        default_bytes = self._default_limit_bytes()
        tier_bytes = self._tier_limit_bytes(tier)

        if override is not None:
            unlimited = bool(_as_int(_row_value(override, "unlimited", 2), 0))
            limit_bytes = _row_value(override, "limit_bytes", 1)
            if unlimited:
                return {
                    "limit_bytes": None,
                    "unlimited": True,
                    "source": "override",
                    "tier": tier,
                    "tier_limit_bytes": tier_bytes,
                    "default_limit_bytes": default_bytes,
                }
            if limit_bytes is not None:
                return {
                    "limit_bytes": max(0, _as_int(limit_bytes, 0)),
                    "unlimited": False,
                    "source": "override",
                    "tier": tier,
                    "tier_limit_bytes": tier_bytes,
                    "default_limit_bytes": default_bytes,
                }

        if tier_bytes == -1:
            return {
                "limit_bytes": None,
                "unlimited": True,
                "source": "tier",
                "tier": tier,
                "tier_limit_bytes": None,
                "default_limit_bytes": default_bytes,
            }
        if tier_bytes is not None:
            return {
                "limit_bytes": tier_bytes,
                "unlimited": False,
                "source": "tier",
                "tier": tier,
                "tier_limit_bytes": tier_bytes,
                "default_limit_bytes": default_bytes,
            }
        if default_bytes < 0:
            return {
                "limit_bytes": None,
                "unlimited": True,
                "source": "default",
                "tier": tier,
                "tier_limit_bytes": None,
                "default_limit_bytes": None,
            }
        return {
            "limit_bytes": default_bytes,
            "unlimited": False,
            "source": "default",
            "tier": tier,
            "tier_limit_bytes": None,
            "default_limit_bytes": default_bytes,
        }

    # ── usage ---------------------------------------------------------------

    def usage_bytes(self, user_id: int) -> int:
        row = self._db.fetch_one(
            "SELECT COALESCE(SUM(size), 0) AS used, COUNT(*) AS files "
            "FROM media_files WHERE uploaded_by = ? AND deleted = 0",
            (int(user_id),),
        )
        return max(0, _as_int(_row_value(row, "used", 0), 0))

    def file_count(self, user_id: int) -> int:
        row = self._db.fetch_one(
            "SELECT COUNT(*) AS files FROM media_files "
            "WHERE uploaded_by = ? AND deleted = 0",
            (int(user_id),),
        )
        return max(0, _as_int(_row_value(row, "files", 0), 0))

    def pending_bytes(self, user_id: int, now_ms: Optional[int] = None) -> int:
        self._ensure_schema()
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        row = self._db.fetch_one(
            "SELECT COALESCE(SUM(bytes), 0) AS pending "
            "FROM storage_quota_reservations "
            "WHERE user_id = ? AND expires_at > ?",
            (int(user_id), int(now)),
        )
        return max(0, _as_int(_row_value(row, "pending", 0), 0))

    def get_status(self, user_id: int) -> Dict[str, Any]:
        """Return usage, limit, and remaining bytes for a user."""
        resolved = self.resolve_limit(user_id)
        used = self.usage_bytes(user_id)
        pending = self.pending_bytes(user_id)
        limit = resolved["limit_bytes"]
        unlimited = bool(resolved["unlimited"])
        consumed = used + pending
        if unlimited or limit is None:
            remaining = None
            percent = 0.0
        else:
            remaining = max(0, int(limit) - consumed)
            percent = round((consumed * 100.0 / limit), 2) if limit > 0 else 100.0
            percent = min(100.0, max(0.0, percent))
        return {
            "used_bytes": used,
            "pending_bytes": pending,
            "limit_bytes": limit,
            "remaining_bytes": remaining,
            "percent_used": percent,
            "unlimited": unlimited,
            "file_count": self.file_count(user_id),
            "source": resolved["source"],
            "tier": resolved["tier"],
            "tier_limit_bytes": resolved["tier_limit_bytes"],
            "default_limit_bytes": resolved["default_limit_bytes"],
        }

    # ── reservations --------------------------------------------------------

    def reserve(self, user_id: int, requested_bytes: int) -> Optional[str]:
        """Reserve ``requested_bytes`` for an upload.

        Returns an opaque token to pass to :meth:`release`, or ``None`` for a
        zero-byte request. Raises ``StorageQuotaExceededError`` when the
        request would cross the effective cap.
        """
        amount = max(0, _as_int(requested_bytes, 0))
        if amount == 0:
            return None

        self._ensure_schema()
        now = int(time.time() * 1000)
        expires = now + self._reservation_ttl_seconds() * 1000
        token = uuid.uuid4().hex

        self._db.begin_transaction()
        try:
            # PostgreSQL needs an explicit per-user lock because two app
            # workers can evaluate the aggregate concurrently. SQLite's
            # single-writer transaction provides the same serialization.
            if getattr(self._db, "type", "") == "postgres":
                self._db.execute("SELECT pg_advisory_xact_lock(?)", (int(user_id),))

            self._db.execute(
                "DELETE FROM storage_quota_reservations "
                "WHERE user_id = ? AND expires_at <= ?",
                (int(user_id), now),
            )
            resolved = self.resolve_limit(user_id)
            used = self.usage_bytes(user_id)
            pending = self.pending_bytes(user_id, now_ms=now)
            limit = resolved["limit_bytes"]
            if not resolved["unlimited"] and limit is not None:
                if used + pending + amount > int(limit):
                    raise StorageQuotaExceededError(
                        "Storage limit reached. Delete some files or ask an "
                        "administrator for more space.",
                        used_bytes=used + pending,
                        limit_bytes=int(limit),
                        requested_bytes=amount,
                    )
            self._db.execute(
                "INSERT INTO storage_quota_reservations "
                "(token, user_id, bytes, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (token, int(user_id), amount, now, expires),
            )
            self._db.commit()
        except Exception:
            try:
                self._db.rollback()
            except Exception as rollback_error:
                logger.debug(f"storage quota rollback failed: {rollback_error}")
            raise
        return token

    def release(self, token: Optional[str]) -> None:
        """Release a reservation. Safe to call repeatedly or with ``None``."""
        if not token:
            return
        try:
            self._ensure_schema()
            self._db.execute(
                "DELETE FROM storage_quota_reservations WHERE token = ?",
                (str(token),),
            )
        except Exception as exc:
            # A stale reservation expires on its own; never turn a successful
            # upload into a failure just because cleanup raced a restart.
            logger.debug("Storage reservation release failed: %s", exc)

    # ── admin overrides -----------------------------------------------------

    def set_override(
        self,
        user_id: int,
        *,
        limit_bytes: Optional[int] = None,
        unlimited: bool = False,
        note: Optional[str] = None,
        updated_by: Optional[int] = None,
    ) -> Dict[str, Any]:
        if unlimited:
            normalized_limit = None
        else:
            if limit_bytes is None:
                raise ValueError("limit_bytes is required unless unlimited is true")
            normalized_limit = max(0, int(limit_bytes))
        self._ensure_schema()
        now = int(time.time() * 1000)
        self._db.execute(
            "INSERT INTO user_storage_quotas "
            "(user_id, limit_bytes, unlimited, note, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "limit_bytes = excluded.limit_bytes, "
            "unlimited = excluded.unlimited, "
            "note = excluded.note, "
            "updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (
                int(user_id),
                normalized_limit,
                1 if unlimited else 0,
                note,
                int(updated_by) if updated_by is not None else None,
                now,
            ),
        )
        return self.get_status(user_id)

    def clear_override(self, user_id: int) -> Dict[str, Any]:
        self._ensure_schema()
        self._db.execute(
            "DELETE FROM user_storage_quotas WHERE user_id = ?", (int(user_id),)
        )
        return self.get_status(user_id)

    def get_override(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self._override_row(user_id)
        if row is None:
            return None
        return {
            "limit_bytes": _row_value(row, "limit_bytes", 1),
            "unlimited": bool(_as_int(_row_value(row, "unlimited", 2), 0)),
            "note": _row_value(row, "note", 3),
            "updated_by": _row_value(row, "updated_by", 4),
            "updated_at": _row_value(row, "updated_at", 5),
        }

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        ensure_schema(self._db)
        self._schema_ready = True


_manager_cache: Dict[int, StorageQuotaManager] = {}


def get_manager(db=None) -> StorageQuotaManager:
    """Return a process-local manager for ``db`` (or the API database)."""
    if db is None:
        import src.api as api

        db = api.get_db()
    if db is None:
        raise RuntimeError("Database unavailable for storage quota")
    key = id(db)
    manager = _manager_cache.get(key)
    if manager is None:
        manager = StorageQuotaManager(db)
        _manager_cache[key] = manager
    return manager
