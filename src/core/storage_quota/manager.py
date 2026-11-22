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

Multi-worker correctness
------------------------

The enforcement read (live usage + other pending reservations + the
authoritative override limit) is folded into the reservation INSERT as a single
conditional ``INSERT ... SELECT ... LEFT JOIN user_storage_quotas`` statement.
A statement is atomic on both SQLite and PostgreSQL, so no worker can observe
an old total and insert a second reservation that pushes the user over quota,
and an admin quota change cannot be bypassed with a limit snapshotted in Python
before the INSERT. PostgreSQL additionally serialises the whole check on a
per-user advisory lock; SQLite serialises the write at the first DELETE inside
the transaction.

The database layer maintains a short-TTL (1s) per-connection query cache. Every
quota *mutation* explicitly invalidates it, so a worker never enforces a limit
against a usage/limit snapshot cached before another worker's upload, delete,
or override change. Reads that only display status may be up to one TTL stale;
enforcement never is.

Tracking is bounded: expired reservations are pruned globally on every reserve
(not only for the reserving user), each user may hold at most
``MAX_ACTIVE_RESERVATIONS_PER_USER`` reservations, and the process-local manager
cache is an LRU with a fixed ceiling.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

import utils.config as config
import utils.logger as logger

from src.core.media.exceptions import StorageQuotaExceededError
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)

from .schema import ensure_schema

MIB = 1024 * 1024
DEFAULT_QUOTA_MB = 5 * 1024
DEFAULT_RESERVATION_TTL_SECONDS = 900
_UNLIMITED_MB = -1

#: An administrator override is a human-entered value that is stored verbatim
#: and later rendered in quota audit views. Bound both fields: a note must not
#: smuggle a log/HTTP-header break via control characters, and a limit must not
#: be an arbitrary-precision integer that overflows byte arithmetic downstream.
MAX_OVERRIDE_NOTE_LENGTH = 500
MAX_OVERRIDE_LIMIT_BYTES = 100 * 1024**4  # 100 TiB
#: Largest quota (in MiB) any config/tier source may declare. Anything above
#: this is a misconfiguration, not a real tier, and is rejected as overflow.
MAX_CONFIG_QUOTA_MB = MAX_OVERRIDE_LIMIT_BYTES // MIB

#: Bounds for the user-facing storage-file listing. Without a page cap a single
#: request could materialise every media row; without a clean path filter an
#: attacker could use ``..``/wildcards to probe paths outside their own files.
MAX_LIST_LIMIT = 200
MAX_LIST_OFFSET = 1_000_000
MAX_LIST_PATH_CHARS = 512

#: A single user may only hold this many in-flight reservations. Without a cap,
#: a client that repeatedly starts uploads and aborts them (or a buggy caller)
#: could grow ``storage_quota_reservations`` without bound within one TTL window.
MAX_ACTIVE_RESERVATIONS_PER_USER = 128

#: Upper bound on the process-local manager cache. Keyed by ``id(db)``; without
#: an LRU ceiling, repeatedly constructed DB objects would leak managers.
MAX_MANAGER_CACHE_ENTRIES = 64

#: Largest single reservation accepted. Keeps the SUM in the guard statement
#: inside signed 64-bit range while staying one byte above the largest legal
#: limit, so clamping an absurd request can never turn a denied upload into an
#: allowed one.
MAX_RESERVATION_BYTES = MAX_OVERRIDE_LIMIT_BYTES + 1

#: One statement performs the quota check and the reservation insert. The
#: guard reads the *authoritative* limit from ``user_storage_quotas`` inside the
#: same statement, so an administrator quota change committed between the
#: Python-side :meth:`resolve_limit` read and this INSERT can never be bypassed
#: with a stale limit snapshot (a multi-worker TOCTOU). The Python-resolved
#: limit is only the fallback for users with no override row. The unlimited
#: branch short-circuits the arithmetic guard for unlimited users; otherwise
#: live usage plus other live reservations plus ``amount`` must fit the limit.
#: Both subqueries are keyed by the reserving user, so no other user's bytes can
#: influence the decision. Scalar clamping uses a portable CASE expression
#: (PostgreSQL has no scalar two-argument MIN/MAX).
_ATOMIC_RESERVE_SQL = """
INSERT INTO storage_quota_reservations
    (token, user_id, bytes, created_at, expires_at)
SELECT ?, ?, ?, ?, ?
FROM (SELECT 1) AS one
LEFT JOIN user_storage_quotas q ON q.user_id = ?
WHERE COALESCE(q.unlimited, ?) = 1
   OR (
    (SELECT COALESCE(SUM(size), 0) FROM media_files
      WHERE uploaded_by = ? AND deleted = 0) +
    (SELECT COALESCE(SUM(bytes), 0) FROM storage_quota_reservations
      WHERE user_id = ? AND expires_at > ?) + ? <=
    CASE
        WHEN q.user_id IS NULL THEN ?
        WHEN COALESCE(q.limit_bytes, 0) <= 0 THEN 0
        WHEN q.limit_bytes >= ? THEN ?
        ELSE q.limit_bytes
    END
   )
"""

#: Distinguishes "caller did not supply an override row" from an explicit
#: ``None`` (no override exists) in :meth:`StorageQuotaManager.resolve_limit`.
_UNSET = object()


def _bounded_override_note(note: Any) -> Optional[str]:
    """Return a safe override note, refusing control characters and bloat."""
    if note is None:
        return None
    text = reject_control_characters(note, "note")
    if len(text) > MAX_OVERRIDE_NOTE_LENGTH:
        raise HardeningViolation(
            f"note exceeds maximum length of {MAX_OVERRIDE_NOTE_LENGTH}"
        )
    return text


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


def _public_override(override: Any) -> Optional[Dict[str, Any]]:
    """Render an override row (tuple or dict) as the API-facing dict.

    Both :meth:`StorageQuotaManager.get_override` and the folded status snapshot
    use this so the admin endpoint gets byte-for-byte identical output whether
    the row came from a dedicated read or from the wider status query.
    """
    if override is None:
        return None
    return {
        "limit_bytes": _row_value(override, "limit_bytes", 1),
        "unlimited": bool(_as_int(_row_value(override, "unlimited", 2), 0)),
        "note": _row_value(override, "note", 3),
        "updated_by": _row_value(override, "updated_by", 4),
        "updated_at": _row_value(override, "updated_at", 5),
    }


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
        # Config integers reach byte arithmetic and LIMIT clauses: validate the
        # type and cap the magnitude instead of silently accepting a huge value
        # (which used to overflow downstream computations).
        mb = bounded_int(
            value,
            "storage.default_quota_mb",
            minimum=-1,
            maximum=MAX_CONFIG_QUOTA_MB,
            default=DEFAULT_QUOTA_MB,
        )
        if mb < 0:
            return -1
        return mb * MIB

    def _reservation_ttl_seconds(self) -> int:
        value = self._storage_config().get(
            "reservation_ttl_seconds", DEFAULT_RESERVATION_TTL_SECONDS
        )
        return bounded_int(
            value,
            "storage.reservation_ttl_seconds",
            minimum=60,
            maximum=3600,
            default=DEFAULT_RESERVATION_TTL_SECONDS,
        )

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
            mb = bounded_int(
                getattr(limits, "max_storage_mb", None),
                "tier.max_storage_mb",
                minimum=-1,
                maximum=MAX_CONFIG_QUOTA_MB,
                default=0,
            )
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

    def resolve_limit(self, user_id: int, override: Any = _UNSET) -> Dict[str, Any]:
        """Return the effective limit and where it came from.

        ``override`` may be supplied by a caller that already fetched the row
        in a wider aggregate query (the quota status hot path does exactly
        that). When omitted, the row is read here as before.
        """
        tier = self._user_tier(user_id)
        if override is _UNSET:
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
                # Clamp (rather than raise) on read so a legacy/oversized row
                # can never overflow byte arithmetic or break a quota read.
                clamped = min(MAX_OVERRIDE_LIMIT_BYTES, max(0, _as_int(limit_bytes, 0)))
                return {
                    "limit_bytes": clamped,
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

    def _status_snapshot(self, user_id: int) -> tuple[int, int, int, Any]:
        """Return every piece of live data ``get_status`` needs in one query.

        The old path issued four separate reads (usage aggregate, file count,
        reservation aggregate, override row) on every quota/usage request.
        Folding them into a single statement removes three database round trips
        from the hottest read endpoint while returning byte-for-byte identical
        values. All subqueries are keyed by the caller's id and run against the
        same indexed columns as the original standalone queries.
        """
        self._ensure_schema()
        uid = int(user_id)
        now = int(time.time() * 1000)
        row = self._db.fetch_one(
            "SELECT "
            "(SELECT COALESCE(SUM(size), 0) FROM media_files "
            " WHERE uploaded_by = ? AND deleted = 0) AS used, "
            "(SELECT COUNT(*) FROM media_files "
            " WHERE uploaded_by = ? AND deleted = 0) AS files, "
            "(SELECT COALESCE(SUM(bytes), 0) FROM storage_quota_reservations "
            " WHERE user_id = ? AND expires_at > ?) AS pending, "
            "q.limit_bytes AS q_limit, q.unlimited AS q_unlimited, "
            "q.note AS q_note, q.updated_by AS q_updated_by, "
            "q.updated_at AS q_updated_at "
            "FROM (SELECT 1) AS one "
            "LEFT JOIN user_storage_quotas q ON q.user_id = ?",
            (uid, uid, uid, now, uid),
        )
        used = max(0, _as_int(_row_value(row, "used", 0), 0))
        files = max(0, _as_int(_row_value(row, "files", 1), 0))
        pending = max(0, _as_int(_row_value(row, "pending", 2), 0))
        q_limit = _row_value(row, "q_limit", 3)
        q_unlimited = _row_value(row, "q_unlimited", 4)
        # A LEFT JOIN with no matching row yields NULL for both columns; that
        # is the "no override" case. A real override with limit_bytes NULL and
        # unlimited 0 still has a non-NULL unlimited column, so it is kept.
        # Carry the full override row here so callers that also need the audit
        # fields (note/updated_by/updated_at) do not re-read the same row; the
        # admin storage endpoints used to issue a second dedicated SELECT.
        override = None
        if q_limit is not None or q_unlimited is not None:
            override = {
                "limit_bytes": q_limit,
                "unlimited": q_unlimited,
                "note": _row_value(row, "q_note", 5),
                "updated_by": _row_value(row, "q_updated_by", 6),
                "updated_at": _row_value(row, "q_updated_at", 7),
            }
        return used, files, pending, override

    def get_status(
        self, user_id: int, *, include_override: bool = False
    ) -> Dict[str, Any]:
        """Return usage, limit, and remaining bytes for a user.

        ``include_override=True`` also returns the full override row under the
        ``override`` key. The audit fields are already present in the single
        :meth:`_status_snapshot` query, so this avoids the extra ``get_override``
        SELECT that the admin storage endpoints previously issued for every
        view/update of a user's quota.
        """
        used, file_count, pending, override = self._status_snapshot(user_id)
        resolved = self.resolve_limit(user_id, override=override)
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
        result: Dict[str, Any] = {
            "used_bytes": used,
            "pending_bytes": pending,
            "limit_bytes": limit,
            "remaining_bytes": remaining,
            "percent_used": percent,
            "unlimited": unlimited,
            "file_count": file_count,
            "source": resolved["source"],
            "tier": resolved["tier"],
            "tier_limit_bytes": resolved["tier_limit_bytes"],
            "default_limit_bytes": resolved["default_limit_bytes"],
        }
        if include_override:
            result["override"] = _public_override(override)
        return result

    def _invalidate_quota_cache(self) -> None:
        """Drop this worker's cached SQL reads before/after a quota mutation.

        The database layer caches ``fetch_one``/``fetch_all`` for up to one TTL
        (1s) per connection. That cache is local to each worker: a write on
        worker A does not clear worker B's copy. Enforcing a quota from such a
        copy would let a user whose usage just grew (or whose limit just shrank)
        keep uploading until the entry expired. Every quota write therefore
        clears the local cache first so the following reads - and the atomic
        guard in :meth:`reserve` - see committed state.
        """
        invalidator = getattr(self._db, "_invalidate_query_cache", None)
        if callable(invalidator):
            try:
                invalidator()
            except Exception as exc:
                # Cache invalidation is best-effort; the SQL guard is the
                # authoritative enforcement point and never reads the cache.
                logger.debug(f"storage quota query-cache invalidation failed: {exc}")

    def _prune_expired_reservations(self, now: int) -> None:
        """Remove expired reservations for *all* users.

        The old cleanup only ran for the user doing the current reserve, so a
        user who uploaded once and never returned left rows forever. Pruning
        globally (and only expired rows) keeps the table bounded by the number
        of uploads in the last TTL window without ever touching a live
        reservation belonging to another user.
        """
        self._db.execute(
            "DELETE FROM storage_quota_reservations WHERE expires_at <= ?", (now,)
        )

    # ── reservations --------------------------------------------------------

    def reserve(self, user_id: int, requested_bytes: int) -> Optional[str]:
        """Reserve ``requested_bytes`` for an upload.

        Returns an opaque token to pass to :meth:`release`, or ``None`` for a
        zero-byte request. Raises ``StorageQuotaExceededError`` when the
        request would cross the effective cap.

        The check and the insert are one atomic SQL statement (see
        :data:`_ATOMIC_RESERVE_SQL`); a read-then-write check would be a TOCTOU
        race between workers. A per-user advisory lock serialises PostgreSQL
        workers and the first write in the transaction serialises SQLite ones.
        """
        amount = max(0, _as_int(requested_bytes, 0))
        if amount == 0:
            return None
        # Clamp absurd requests before they reach signed 64-bit SUM arithmetic.
        amount = min(amount, MAX_RESERVATION_BYTES)

        self._ensure_schema()
        self._invalidate_quota_cache()
        now = int(time.time() * 1000)
        expires = now + self._reservation_ttl_seconds() * 1000
        token = uuid.uuid4().hex
        uid = int(user_id)

        # Bound the table for users who never come back to upload again. This is
        # outside the transaction: it only deletes already-expired rows, so it
        # can never remove a live reservation belonging to another user.
        self._prune_expired_reservations(now)
        self._invalidate_quota_cache()

        self._db.begin_transaction()
        try:
            # PostgreSQL needs an explicit per-user lock because two app
            # workers can evaluate the aggregate concurrently. SQLite's
            # single-writer transaction provides the same serialization; the
            # DELETE below is deliberately the first write so the write lock is
            # taken before any read (avoids a deferred-transaction upgrade race).
            if getattr(self._db, "type", "") == "postgres":
                self._db.execute("SELECT pg_advisory_xact_lock(?)", (uid,))

            self._db.execute(
                "DELETE FROM storage_quota_reservations "
                "WHERE user_id = ? AND expires_at <= ?",
                (uid, now),
            )

            resolved = self.resolve_limit(uid)
            limit = resolved["limit_bytes"]
            unlimited = bool(resolved["unlimited"])

            # Bound per-user tracking: a client that starts many uploads and
            # aborts them must not be able to grow the ledger without limit.
            active = self._db.fetch_one(
                "SELECT COUNT(*) AS active FROM storage_quota_reservations "
                "WHERE user_id = ? AND expires_at > ?",
                (uid, now),
            )
            if max(0, _as_int(_row_value(active, "active", 0), 0)) >= (
                MAX_ACTIVE_RESERVATIONS_PER_USER
            ):
                raise StorageQuotaExceededError(
                    "Too many uploads in progress. Retry in a moment.",
                    limit_bytes=int(limit) if limit is not None else 0,
                    requested_bytes=amount,
                )

            # A single statement checks usage + other pending reservations and
            # inserts the reservation only if it still fits. No worker can see
            # a stale total and push the user over quota.
            cursor = self._db.execute(
                _ATOMIC_RESERVE_SQL,
                (
                    token,
                    uid,
                    amount,
                    now,
                    expires,
                    uid,  # LEFT JOIN user_storage_quotas
                    1 if unlimited else 0,  # fallback when no override row
                    uid,  # media_files usage subquery
                    uid,  # reservation pending subquery
                    now,  # expires_at > now
                    amount,
                    int(limit) if limit is not None else 0,  # no-override fallback
                    MAX_OVERRIDE_LIMIT_BYTES,  # clamp legacy override rows
                    MAX_OVERRIDE_LIMIT_BYTES,
                ),
            )
            if getattr(cursor, "rowcount", 0) != 1:
                used = self.usage_bytes(uid) + self.pending_bytes(uid, now_ms=now)
                raise StorageQuotaExceededError(
                    "Storage limit reached. Delete some files or ask an "
                    "administrator for more space.",
                    used_bytes=used,
                    limit_bytes=int(limit) if limit is not None else 0,
                    requested_bytes=amount,
                )
            self._db.commit()
        except Exception:
            try:
                self._db.rollback()
            except Exception as rollback_error:
                logger.debug(f"storage quota rollback failed: {rollback_error}")
            raise
        self._invalidate_quota_cache()
        return token

    def release(self, token: Optional[str]) -> None:
        """Release a reservation. Safe to call repeatedly or with ``None``.

        Tokens are unguessable uuid4 values, so deleting by token can never
        release another user's reservation.
        """
        if not token:
            return
        try:
            self._ensure_schema()
            self._invalidate_quota_cache()
            self._db.execute(
                "DELETE FROM storage_quota_reservations WHERE token = ?",
                (str(token),),
            )
            self._invalidate_quota_cache()
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
        include_override: bool = False,
    ) -> Dict[str, Any]:
        if unlimited:
            normalized_limit = None
        else:
            if limit_bytes is None:
                raise ValueError("limit_bytes is required unless unlimited is true")
            normalized_limit = bounded_int(
                limit_bytes,
                "limit_bytes",
                minimum=0,
                maximum=MAX_OVERRIDE_LIMIT_BYTES,
            )
        safe_note = _bounded_override_note(note)
        # Every id written by an override is an untrusted-number boundary too:
        # a negative or >snowflake value would poison audit rows and lookups.
        safe_user_id = parse_id(user_id, "user_id")
        safe_updated_by = (
            parse_id(updated_by, "updated_by") if updated_by is not None else None
        )
        self._ensure_schema()
        self._invalidate_quota_cache()
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
                safe_user_id,
                normalized_limit,
                1 if unlimited else 0,
                safe_note,
                safe_updated_by,
                now,
            ),
        )
        self._invalidate_quota_cache()
        return self.get_status(user_id, include_override=include_override)

    def clear_override(
        self, user_id: int, *, include_override: bool = False
    ) -> Dict[str, Any]:
        self._ensure_schema()
        self._invalidate_quota_cache()
        self._db.execute(
            "DELETE FROM user_storage_quotas WHERE user_id = ?", (int(user_id),)
        )
        self._invalidate_quota_cache()
        return self.get_status(user_id, include_override=include_override)

    def get_override(self, user_id: int) -> Optional[Dict[str, Any]]:
        return _public_override(self._override_row(user_id))

    def list_user_files(
        self,
        user_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        path_prefix: Optional[str] = None,
        before_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return a bounded page of the user's live media rows.

        Pagination is range-checked and the optional path filter is escaped,
        control-character-free and traversal-free, so a caller cannot use a
        huge offset, a ``..`` component or LIKE wildcards to scan outside their
        own files.

        ``before_id`` is an opt-in keyset cursor: the ``id`` of the last row the
        caller saw. ``ORDER BY id DESC`` already matches the index, so a keyset
        page is a single ``id < ?`` seek with a bound ``LIMIT`` instead of an
        OFFSET walk that deepens with the page number. The ``uploaded_by``
        scope stays on both branches, so a cursor taken from someone else's rows
        can never widen the result.
        """
        uid = parse_id(user_id, "user_id")
        limit = bounded_int(limit, "limit", minimum=1, maximum=MAX_LIST_LIMIT)
        offset = bounded_int(offset, "offset", minimum=0, maximum=MAX_LIST_OFFSET)

        cursor: Optional[int] = None
        if before_id is not None:
            # Mixing the two paging styles would silently drop one of them, so
            # the combination is refused before any query runs.
            if offset:
                raise HardeningViolation("before_id cannot be combined with offset")
            if (
                isinstance(before_id, bool)
                or not isinstance(before_id, str)
                or not before_id.isascii()
                or not before_id.isdigit()
                or len(before_id) > 19
            ):
                raise HardeningViolation("invalid before_id cursor")
            cursor = int(before_id)
            if cursor <= 0 or cursor > 2**63 - 1:
                raise HardeningViolation("invalid before_id cursor")

        prefix: Optional[str] = None
        if path_prefix is not None and str(path_prefix) != "":
            prefix = reject_control_characters(path_prefix, "path")
            if len(prefix) > MAX_LIST_PATH_CHARS:
                raise HardeningViolation("path filter exceeds the length limit")
            if prefix.startswith("/"):
                raise HardeningViolation("path filter must be relative")
            if any(part == ".." for part in prefix.replace("\\", "/").split("/")):
                raise HardeningViolation("path filter must not contain traversal")
            prefix = prefix.strip("/")

        where = "uploaded_by = ? AND deleted = 0"
        params: list = [uid]
        if prefix:
            escaped = (
                prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            where += " AND storage_path LIKE ? ESCAPE '\\'"
            params.append(escaped + "%")

        # Per-file metadata is selected in the SAME statement as the page of
        # rows. Resolving ``content_type``/``checksum``/``scan_status`` with a
        # follow-up ``fetch_one`` per file would turn this bounded listing into
        # an N+1 read (one query for the page plus one per returned file), which
        # grows linearly with the page size. Joining the metadata columns here
        # keeps the whole listing at exactly one query regardless of page size.
        # ``metadata`` (free-form JSON) and ``uploaded_by`` (a snowflake) are
        # deliberately not exposed through this user-facing response.
        columns = (
            "SELECT id, filename, original_filename, size, storage_path, "
            "uploaded_at, content_type, media_type, storage_backend, "
            "checksum, scan_status, scan_result "
        )
        if cursor is not None:
            rows = self._db.fetch_all(
                columns + f"FROM media_files WHERE {where} AND id < ? "
                "ORDER BY id DESC LIMIT ?",
                tuple(params) + (cursor, limit),
            )
        else:
            rows = self._db.fetch_all(
                columns + f"FROM media_files WHERE {where} "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                tuple(params) + (limit, offset),
            )
        # Ids cross the API boundary as strings like every other snowflake;
        # never leak a bare int from a free-form response.
        files = []
        for row in rows:
            item = dict(row)
            if "id" in item and item["id"] is not None:
                item["id"] = str(item["id"])
            files.append(item)
        return {
            "files": files,
            "limit": limit,
            # A keyset page has no offset: the cursor is the position.
            "offset": 0 if cursor is not None else offset,
        }

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        ensure_schema(self._db)
        self._schema_ready = True


_manager_cache: Dict[int, StorageQuotaManager] = {}


def get_manager(db=None) -> StorageQuotaManager:
    """Return a process-local manager for ``db`` (or the API database).

    The cache is a bounded LRU: an unbounded dict keyed by ``id(db)`` leaked a
    manager for every DB object ever constructed (and could hand back a manager
    bound to a collected object whose id was reused). Evicting a manager is safe
    - it only drops the ``_schema_ready`` flag, which is re-established lazily.
    """
    if db is None:
        import src.api as api

        db = api.get_db()
    if db is None:
        raise RuntimeError("Database unavailable for storage quota")
    key = id(db)
    # pop + reinsert keeps the dict in least-recently-used order.
    manager = _manager_cache.pop(key, None)
    if manager is None:
        manager = StorageQuotaManager(db)
    _manager_cache[key] = manager
    while len(_manager_cache) > MAX_MANAGER_CACHE_ENTRIES:
        oldest = next(iter(_manager_cache))
        _manager_cache.pop(oldest, None)
    return manager
