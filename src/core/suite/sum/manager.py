"""PlexiSum workbook service — file-backed, permission-aware.

Each workbook is an independent SQLite database file (``.plexisum``) stored
through the main file-storage adapter (S3/local via the media blob boundary).
The main database never stores workbook cells; it keeps only identity,
ownership, sharing ACL, range permissions, snapshot pointers, and the revision
counter used for optimistic concurrency.

Every read opens the workbook file from storage and every mutation writes a
new immutable versioned blob before the metadata row advances, which gives
readers snapshot isolation and writers a compare-and-swap gate.
"""

import base64
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import OrderedDict

import utils.config as config
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.base import BaseManager

from .formulas import FormulaEvaluator, is_error
from .schema import (
    PLEXISUM_FORMAT_VERSION,
    SUM_OPERATION_TYPES,
    SUM_VALUE_TYPES,
    ensure_feature_tables,
)
from .segments import SegmentEncoder

MAX_SNAPSHOT_CELLS = 200_000
MAX_CSV_ROWS = 10_000
MAX_CSV_COLUMNS = 100
MAX_INDEXES_PER_SHEET = 8
INDEX_PROMOTION_THRESHOLD = 50

# Structural (row/column) operation bounds.
STRUCTURAL_OPERATION_TYPES = (
    "insert_rows",
    "delete_rows",
    "insert_columns",
    "delete_columns",
)
MAX_STRUCTURAL_CHANGE = 10_000
MAX_STRUCTURAL_CELLS = 200_000

# Snapshots beyond this count are garbage-collected (oldest first) when a
# new snapshot is created.
MAX_SNAPSHOTS_KEPT = 50
# Bound compensation memory during account/workbook purge. A purge that would
# exceed this limit fails before deleting that blob batch, preserving metadata
# pointers for a later retry instead of risking worker OOM.
MAX_PURGE_BACKUP_BYTES = 256 * 1024 * 1024

# Keep this many immutable revision blobs per workbook after a successful
# commit; older revisions are unreachable through any read path (the metadata
# row only tracks the current blob) and are garbage-collected to stop storage
# growing by the full file size on every edit.
REVISION_KEEP_COUNT = 5

# Bounds for the in-process workbook-file read cache. Blobs are immutable and
# addressed by (workbook_id, revision), so cached entries can never go stale.
WORKBOOK_CACHE_MAX_ENTRIES = 32
WORKBOOK_CACHE_MAX_BYTES = 256 * 1024 * 1024


class _BoundedFileCache:
    """Thread-safe bounded LRU for immutable workbook file bytes.

    Every read currently has to fetch the whole ``.plexisum`` file from the
    storage adapter (S3/local); the cache eliminates repeated full-file
    downloads for hot workbooks without ever serving stale data because the
    key includes the immutable revision. Bounded by entry count and total
    bytes so a few large workbooks cannot exhaust worker memory.
    """

    def __init__(
        self,
        max_entries: int = WORKBOOK_CACHE_MAX_ENTRIES,
        max_bytes: int = WORKBOOK_CACHE_MAX_BYTES,
    ) -> None:
        self._entries: "OrderedDict[Tuple[int, int], bytes]" = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._lock = threading.RLock()

    def get(self, key: Tuple[int, int]) -> Optional[bytes]:
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: Tuple[int, int], value: bytes) -> None:
        with self._lock:
            if key in self._entries:
                self._bytes -= len(self._entries.pop(key))
            self._entries[key] = value
            self._bytes += len(value)
            while (
                len(self._entries) > self._max_entries or self._bytes > self._max_bytes
            ) and self._entries:
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= len(evicted)

    def drop_workbook(self, workbook_id: int) -> None:
        with self._lock:
            stale = [key for key in self._entries if key[0] == int(workbook_id)]
            for key in stale:
                self._bytes -= len(self._entries.pop(key))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0


_WORKBOOK_FILE_CACHE = _BoundedFileCache()

# In-process workbook presence registry: workbook_id -> user_id -> record.
# Clients heartbeat via PUT /workbooks/{id}/presence; stale entries older
# than PRESENCE_TTL_SECONDS are pruned on read. Single-worker by design (the
# workbook file cache above is likewise in-process); multi-worker presence
# would ride the cross-worker Valkey pub/sub introduced for events.
PRESENCE_TTL_SECONDS = 30
_WORKBOOK_PRESENCE: Dict[int, Dict[int, Dict[str, Any]]] = {}
_PRESENCE_LOCK = threading.RLock()


# ── Coalesced write sessions ────────────────────────────────────────────────
# Cell edits are acknowledged durably (main-database ``sum_operations`` log +
# revision CAS) and applied to a live in-memory copy of the workbook; the
# immutable blob is only promoted when the write buffer crosses a threshold.
# This collapses the previous one-full-file-upload-per-edit into one upload per
# flush window while crash safety is preserved by the durable log, which
# materialization replays on top of the stored blob. The registry is module
# level (like ``_WORKBOOK_FILE_CACHE``) so it survives per-request manager
# instances and is shared across the process's workers.

SUM_SESSION_MAX_ENTRIES = 32  # live in-memory workbook copies per process
SUM_FLUSH_MAX_OPS = 25  # unflushed ops before a forced flush
SUM_FLUSH_MAX_MS = 2000  # age of the oldest unflushed op before flush

_WORKBOOK_SESSIONS: "Dict[int, _LiveSession]" = {}
_WORKBOOK_SESSION_LOCKS: "Dict[int, threading.RLock]" = {}
_SESSION_GUARD = threading.RLock()
# Temp-file paths for runtimes without sqlite ``deserialize`` (id -> path),
# removed when the session connection is closed.
_SESSION_TMP_PATHS: "Dict[int, str]" = {}


class _LiveSession:
    """An in-memory workbook copy materialized at the current revision.

    ``loaded_blob_revision`` is the revision the stored blob is materialized
    at; the connection itself always reflects ``current_revision`` because
    unflushed operations are replayed into it. ``pending`` counts operations
    applied locally since the last flush and drives the coalescing window.
    """

    __slots__ = (
        "connection",
        "loaded_blob_revision",
        "pending",
        "last_flush_ms",
        "last_access_ms",
    )

    def __init__(
        self,
        connection: "sqlite3.Connection",
        loaded_blob_revision: int,
    ) -> None:
        self.connection = connection
        self.loaded_blob_revision = int(loaded_blob_revision)
        self.pending = 0
        self.last_flush_ms = int(time.time() * 1000)
        self.last_access_ms = self.last_flush_ms


def _prune_presence(now: Optional[float] = None) -> None:
    """Drop presence records whose heartbeats are older than the TTL."""
    if now is None:
        now = time.time()
    with _PRESENCE_LOCK:
        for workbook_id in list(_WORKBOOK_PRESENCE):
            stale = [
                uid
                for uid, rec in _WORKBOOK_PRESENCE[workbook_id].items()
                if now - float(rec.get("ts", 0)) > PRESENCE_TTL_SECONDS
            ]
            for uid in stale:
                del _WORKBOOK_PRESENCE[workbook_id][uid]
            if not _WORKBOOK_PRESENCE[workbook_id]:
                del _WORKBOOK_PRESENCE[workbook_id]


def _column_letter(index: int) -> str:
    """Convert a zero-based column index to its spreadsheet label (0 -> A)."""
    label = ""
    value = int(index) + 1
    while value > 0:
        remainder = (value - 1) % 26
        label = chr(65 + remainder) + label
        value = (value - 1) // 26
    return label


def _classify_csv_value(
    row_index: int, column_index: int, value: Any
) -> Dict[str, Any]:
    """Infer number/boolean/text types from a CSV cell (mirrors the client)."""
    text = str(value)
    if text == "TRUE":
        return {
            "row": row_index,
            "column": column_index,
            "value_type": "boolean",
            "value": True,
        }
    if text == "FALSE":
        return {
            "row": row_index,
            "column": column_index,
            "value_type": "boolean",
            "value": False,
        }
    if re.match(r"^-?\d+(\.\d+)?$", text.strip()):
        return {
            "row": row_index,
            "column": column_index,
            "value_type": "number",
            "value": float(text),
        }
    return {
        "row": row_index,
        "column": column_index,
        "value_type": "text",
        "value": text,
    }


class SumNotFoundError(LookupError):
    """Raised when a workbook, sheet, or snapshot is unavailable."""


class SumPermissionError(PermissionError):
    """Raised when a caller lacks workbook access."""


class SumValidationError(ValueError):
    """Raised for malformed or over-sized workbook operations."""


class SumConflictError(RuntimeError):
    """Raised when an operation's base revision is stale."""


class SumStorageError(RuntimeError):
    """Raised when the workbook file cannot be stored or retrieved."""


class BlobStore:
    """Storage-adapter boundary for server-authored workbook files."""

    def store(self, data: bytes, key: str) -> str:
        raise NotImplementedError

    def retrieve(self, path: str) -> bytes:
        raise NotImplementedError

    def delete(self, path: str) -> bool:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def size(self, path: str) -> int:
        raise NotImplementedError


class MediaBlobStore(BlobStore):
    """Store workbook files through the media storage adapter (S3/local)."""

    def __init__(self, media: Any) -> None:
        self._media = media

    def store(self, data: bytes, key: str) -> str:
        return self._media.store_blob(data, key)

    def retrieve(self, path: str) -> bytes:
        return self._media.retrieve_blob(path)

    def delete(self, path: str) -> bool:
        try:
            return bool(self._media.delete_blob(path))
        except Exception:
            return False

    def exists(self, path: str) -> bool:
        # Use the media module's public server-blob boundary when available. A
        # Compatibility facades may predate blob_exists(). Retrieve is still a
        # reliable existence probe for those adapters and avoids treating every
        # successful delete as unconfirmed forever.
        checker = getattr(self._media, "blob_exists", None)
        if callable(checker):
            return bool(checker(path))
        try:
            self._media.retrieve_blob(path)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            # An unavailable/unauthorized backend is not proof of absence.
            # Report present so _delete_blob_confirmed fails closed and keeps
            # the metadata pointer for a later retry.
            return True

    def size(self, path: str) -> int:
        try:
            return int(self._media.get_blob_size(path))
        except Exception:
            return 0


class LocalBlobStore(BlobStore):
    """Filesystem fallback used when media is unavailable (tests, embedded)."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _resolve(self, path: str) -> str:
        if (
            not isinstance(path, str)
            or path.startswith((".", "/", "\\"))
            or ".." in path
        ):
            raise ValueError("Invalid blob path")
        return os.path.join(self._base_dir, path)

    def store(self, data: bytes, key: str) -> str:
        target = self._resolve(key)
        directory = os.path.dirname(target)
        os.makedirs(directory, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".plexisum-", suffix=".tmp", dir=directory, delete=False
            ) as handle:
                temporary = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
            return key
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def retrieve(self, path: str) -> bytes:
        with open(self._resolve(path), "rb") as handle:
            return handle.read()

    def delete(self, path: str) -> bool:
        try:
            os.unlink(self._resolve(path))
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def exists(self, path: str) -> bool:
        try:
            return os.path.isfile(self._resolve(path))
        except OSError:
            return False

    def size(self, path: str) -> int:
        try:
            return int(os.path.getsize(self._resolve(path)))
        except OSError:
            return 0


class SumManager(BaseManager):
    """Authoritative file-backed bounded data layer for PlexiSum."""

    MAX_VIEWPORT_CELLS = 5000
    MAX_OPERATION_CELLS = 2000

    def __init__(
        self,
        db: Any,
        servers_module: Optional[Any] = None,
        media: Optional[Any] = None,
        storage_dir: Optional[str] = None,
    ) -> None:
        super().__init__(db)
        self._servers = servers_module
        if media is not None:
            self._blob = MediaBlobStore(media)
        else:
            environment = (
                str(config.get("application.environment", "development"))
                .strip()
                .lower()
            )
            local_opt_in = os.getenv("PLEXICHAT_SUM_ALLOW_LOCAL_STORAGE", "").lower()
            if (
                storage_dir is None
                and environment
                not in {
                    "development",
                    "test",
                    "testing",
                }
                and local_opt_in not in {"1", "true", "yes"}
            ):
                raise SumStorageError(
                    "PlexiSum requires the configured media storage adapter"
                )
            base_dir = storage_dir or os.path.join(os.getcwd(), "data", "sum")
            self._blob = LocalBlobStore(base_dir)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _audit(
        self,
        action: str,
        resource_type: str,
        resource_id: Optional[int],
        actor_id: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write a best-effort Suite audit record for Sum mutations."""
        try:
            self._db.execute(
                "INSERT INTO suite_audit_log "
                "(id, domain, action, resource_type, resource_id, actor_id, details_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._generate_id(),
                    "sum",
                    action,
                    resource_type,
                    resource_id,
                    actor_id,
                    self._json(details, {}) if details else None,
                    int(time.time() * 1000),
                ),
            )
        except Exception:
            import utils.logger as logger

            logger.warning("PlexiSum audit write failed", exc_info=True)

    # ── realtime collaboration ───────────────────────────────────────────────

    def _authorized_user_ids(self, workbook: Dict[str, Any]) -> List[int]:
        """Every user who may view this workbook (owner + live grants + server members)."""
        ids: Dict[int, None] = {int(workbook["owner_id"]): None}
        try:
            rows = self._db.fetch_all(
                "SELECT subject_id FROM sum_grants WHERE workbook_id = ? "
                "AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
                (int(workbook["id"]), int(time.time() * 1000)),
            )
            for row in rows:
                ids[int(row["subject_id"])] = None
        except Exception:
            import utils.logger as logger

            logger.error(
                f"PlexiSum: failed to load grants for workbook {workbook.get('id')}; "
                "grant holders excluded from authorized viewers",
                exc_info=True,
            )
        server_id = workbook.get("server_id")
        if (
            workbook.get("scope") == "server"
            and server_id is not None
            and self._servers is not None
        ):
            try:
                for member_id in (
                    self._servers.get_member_user_ids(int(server_id)) or []
                ):
                    ids[int(member_id)] = None
            except Exception:
                import utils.logger as logger

                logger.error(
                    f"PlexiSum: failed to load server members for workbook "
                    f"{workbook.get('id')} (server {server_id}); "
                    "server members excluded from authorized viewers",
                    exc_info=True,
                )
        return list(ids)

    def _emit_update(
        self,
        workbook: Optional[Dict[str, Any]],
        revision: int,
        actor_id: int,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Broadcast a realtime WORKBOOK_UPDATE to everyone with access.

        The actor is excluded (they already applied the change); every other
        viewer refreshes their viewport when the event lands. Non-raising so
        a dispatch failure can never break the underlying mutation.
        """
        if not workbook:
            return
        try:
            from src.core.events import gateway_emit

            payload = {
                "workbook_id": str(workbook["id"]),
                "revision": int(revision),
                "actor_id": int(actor_id),
                "updated_at": int(time.time() * 1000),
            }
            if extra:
                payload.update(extra)
            gateway_emit.emit_workbook_update(
                workbook_id=int(workbook["id"]),
                revision=int(revision),
                actor_id=int(actor_id),
                data=payload,
                user_ids=self._authorized_user_ids(workbook),
                exclude_user_ids=[int(actor_id)],
            )
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum update emit failed", exc_info=True)

    def set_presence(
        self,
        user_id: int,
        workbook_id: int,
        username: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Record a viewer heartbeat and return the current presence list."""
        self._workbook(user_id, workbook_id)
        now = time.time()
        with _PRESENCE_LOCK:
            _WORKBOOK_PRESENCE.setdefault(int(workbook_id), {})[int(user_id)] = {
                "user_id": int(user_id),
                "username": username or f"user-{user_id}",
                "ts": now,
            }
        return self.list_presence(user_id, workbook_id)

    def list_presence(self, user_id: int, workbook_id: int) -> List[Dict[str, Any]]:
        """Return current viewers of a workbook, oldest heartbeats pruned."""
        self._workbook(user_id, workbook_id)
        _prune_presence()
        with _PRESENCE_LOCK:
            records = list(_WORKBOOK_PRESENCE.get(int(workbook_id), {}).values())
        return [
            {
                "user_id": int(rec["user_id"]),
                "username": rec.get("username"),
                "last_seen": int(float(rec.get("ts", 0)) * 1000),
            }
            for rec in sorted(records, key=lambda r: int(r["user_id"]))
        ]

    @staticmethod
    def _extract_mentions(body: Optional[str]) -> List[str]:
        """Pull ``@username`` tokens from comment text (2-32 char usernames)."""
        return re.findall(r"@([A-Za-z0-9_.-]{2,32})", str(body or ""))

    def _notify_comment_mentions(
        self,
        comment: Dict[str, Any],
        workbook: Dict[str, Any],
    ) -> List[int]:
        """Create mention notifications for @usernames in a comment body.

        Resolves usernames to user ids, writes rows to ``notif_notifications``
        (so the bell/API lists them), dispatches a NOTIFICATION_CREATE realtime
        event, and returns the mentioned ids so the WORKBOOK_UPDATE event can
        carry them for in-workspace toasts. Best-effort and non-raising.
        """
        mentioned_ids: List[int] = []
        try:
            usernames = self._extract_mentions(comment.get("body"))
            if not usernames:
                return mentioned_ids
            placeholders = ",".join("?" for _ in usernames)
            rows = self._db.fetch_all(
                f"SELECT id FROM users WHERE LOWER(username) IN ({placeholders}) "
                "AND deleted = 0",
                [name.lower() for name in usernames],
            )
            author_id = int(comment.get("author_id") or 0)
            now = int(time.time() * 1000)
            preview = (str(comment.get("body") or ""))[:120]
            from src.core.events.types import EventType
            from src.core import events

            for row in rows:
                target_id = int(row["id"])
                if target_id == author_id:
                    continue
                mentioned_ids.append(target_id)
                notif_id = self._generate_id()
                self._db.execute(
                    "INSERT INTO notif_notifications "
                    "(id, user_id, sender_id, message_id, conversation_id, server_id, "
                    "channel_id, thread_id, mention_type, content_preview, "
                    "content_preview_encrypted, read, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)",
                    (
                        notif_id,
                        target_id,
                        author_id,
                        0,
                        0,
                        workbook.get("server_id"),
                        0,
                        0,
                        "user",
                        preview,
                        now,
                    ),
                )
                try:
                    events.dispatch(
                        events.Event(
                            event_type=EventType.NOTIFICATION_CREATE,
                            data={
                                "id": str(notif_id),
                                "user_id": str(target_id),
                                "sender_id": str(author_id),
                                "message_id": None,
                                "conversation_id": None,
                                "server_id": str(workbook.get("server_id"))
                                if workbook.get("server_id")
                                else None,
                                "mention_type": "user",
                                "content_preview": preview,
                                "read": False,
                                "created_at": now,
                            },
                        ),
                        user_ids=[target_id],
                    )
                except Exception:
                    pass
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum mention notification failed", exc_info=True)
        return mentioned_ids

    @staticmethod
    def _json(value: Any, fallback: Any = None) -> str:
        try:
            return json.dumps(
                value if value is not None else fallback, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise SumValidationError("Value must be JSON serializable") from exc

    @staticmethod
    def _decode(value: Optional[str], fallback: Any = None) -> Any:
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback

    def _is_member(self, user_id: int, server_id: int) -> bool:
        if self._servers is None:
            return False
        try:
            return self._servers.get_member(server_id, user_id) is not None
        except Exception:
            return False

    def _has_grant(
        self, workbook_id: int, user_id: int, permission: str = "view"
    ) -> bool:
        now = int(time.time() * 1000)
        rows = self._db.fetch_all(
            "SELECT permissions FROM sum_grants WHERE workbook_id = ? AND subject_id = ? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
            (int(workbook_id), int(user_id), now),
        )
        if not rows:
            return False
        for row in rows:
            perms = self._decode(row.get("permissions"), [])
            if isinstance(perms, list) and permission in perms:
                return True
            if isinstance(perms, str) and permission in perms.split(","):
                return True
        return False

    def _authorize(
        self, workbook: Dict[str, Any], user_id: int, mutation: bool = False
    ) -> None:
        if int(workbook["owner_id"]) == int(user_id):
            return
        if self._has_grant(int(workbook["id"]), int(user_id), "edit"):
            return
        if not mutation and self._has_grant(int(workbook["id"]), int(user_id), "view"):
            return
        server_id = workbook.get("server_id")
        if (
            workbook["scope"] == "server"
            and server_id is not None
            and self._is_member(user_id, int(server_id))
        ):
            if not mutation:
                return
            try:
                if self._servers and self._servers.has_permission(
                    user_id, int(server_id), "server.manage"
                ):
                    return
            except Exception:
                pass
        if mutation and self._has_grant(int(workbook["id"]), int(user_id), "view"):
            raise SumPermissionError("You have view-only access to this workbook")
        raise SumPermissionError("Not authorized to access this workbook")

    def _workbook(
        self, user_id: int, workbook_id: int, mutation: bool = False
    ) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM sum_workbooks WHERE id = ? AND deleted = 0",
            (int(workbook_id),),
        )
        if not row:
            raise SumNotFoundError("Workbook not found")
        self._authorize(row, user_id, mutation=mutation)
        return row

    def _viewer_permissions(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: Optional[int] = None,
        workbook: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Effective access for ``user_id`` on a workbook.

        Returns ``can_view``/``can_edit`` plus the per-sheet ``read_only``
        ranges that apply to this user so clients can render permission-aware
        editing without trusting client-supplied capability labels. The owner
        bypasses range protections; every other authorized user (including
        edit grantees) is blocked inside ranges the owner locked read-only.
        """
        if workbook is None:
            workbook = self._workbook(user_id, workbook_id)
        wb_id = int(workbook["id"])
        is_owner = int(workbook["owner_id"]) == int(user_id)

        can_edit = is_owner
        if not can_edit and self._has_grant(wb_id, int(user_id), "edit"):
            can_edit = True
        if not can_edit:
            server_id = workbook.get("server_id")
            if (
                workbook.get("scope") == "server"
                and server_id is not None
                and self._is_member(int(user_id), int(server_id))
            ):
                try:
                    if self._servers and self._servers.has_permission(
                        int(user_id), int(server_id), "server.manage"
                    ):
                        can_edit = True
                except Exception:
                    pass

        reason = None
        if not can_edit:
            has_view = self._has_grant(wb_id, int(user_id), "view") or self._has_grant(
                wb_id, int(user_id), "edit"
            )
            if not has_view:
                server_id = workbook.get("server_id")
                has_view = (
                    workbook.get("scope") == "server"
                    and server_id is not None
                    and self._is_member(int(user_id), int(server_id))
                )
            # Unreachable via the public API (``_workbook`` already authorized
            # view access), but kept explicit for defensive callers.
            reason = None if has_view else "not_authorized"
            if has_view:
                reason = "view_only"

        read_only_ranges: List[Dict[str, Any]] = []
        if not is_owner and sheet_id is not None:
            rows = self._db.fetch_all(
                "SELECT id, sheet_id, row_start, row_end, column_start, "
                "column_end, permission, created_at FROM sum_range_permissions "
                "WHERE workbook_id = ? AND sheet_id = ? AND permission = 'read_only' "
                "ORDER BY row_start, column_start",
                (wb_id, int(sheet_id)),
            )
            for row in rows:
                read_only_ranges.append(
                    {
                        "id": str(row["id"]),
                        "workbook_id": str(wb_id),
                        "sheet_id": str(row["sheet_id"]),
                        "row_start": int(row["row_start"]),
                        "row_end": int(row["row_end"]),
                        "column_start": int(row["column_start"]),
                        "column_end": int(row["column_end"]),
                        "permission": str(row["permission"]),
                        "created_at": int(row["created_at"]),
                    }
                )

        return {
            "can_view": True,
            "can_edit": bool(can_edit),
            "reason": reason,
            "read_only_ranges": read_only_ranges,
        }

    # ── workbook file lifecycle ──────────────────────────────────────────────

    def _file_bytes(self, workbook_id: int) -> bytes:
        """Return the workbook's current materialized state as bytes.

        The live session already includes unflushed operations, so this
        serializes the in-memory copy instead of downloading a blob. Used by
        read paths that need an immutable byte snapshot (downloads, snapshots,
        full-file rewrites); hot viewport reads query the session directly.
        """
        with self._session_lock(int(workbook_id)):
            connection = self._materialized_connection_unlocked(int(workbook_id))
            return self._serialize_connection(connection)

    def _cache_current_file(self, workbook_id: int, revision: int, data: bytes) -> None:
        """Publish just-committed file bytes to the read cache."""
        _WORKBOOK_FILE_CACHE.put((int(workbook_id), int(revision)), data)

    # ── live sessions (coalesced writes) ────────────────────────────────────

    @staticmethod
    def _session_lock(workbook_id: int) -> threading.RLock:
        """Return the per-workbook session lock, shared across instances.

        Touching the workbook here also marks the session recently used so
        eviction never races an in-flight holder (a lock holder is by
        definition not the least-recently-used candidate).
        """
        with _SESSION_GUARD:
            lock = _WORKBOOK_SESSION_LOCKS.setdefault(
                int(workbook_id), threading.RLock()
            )
        session = _WORKBOOK_SESSIONS.get(int(workbook_id))
        if session is not None:
            session.last_access_ms = int(time.time() * 1000)
        return lock

    @staticmethod
    def _drop_session(workbook_id: int) -> None:
        """Discard a live session; its unflushed ops stay durable in the log."""
        with _SESSION_GUARD:
            session = _WORKBOOK_SESSIONS.pop(int(workbook_id), None)
        if session is not None:
            SumManager._close_session_connection(session.connection)

    @staticmethod
    def _close_session_connection(connection: "sqlite3.Connection") -> None:
        try:
            connection.close()
        except sqlite3.Error:
            pass
        path = _SESSION_TMP_PATHS.pop(id(connection), None)
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _materialized_connection(self, workbook_id: int) -> "sqlite3.Connection":
        with self._session_lock(int(workbook_id)):
            return self._materialized_connection_unlocked(int(workbook_id))

    def _materialized_connection_unlocked(
        self, workbook_id: int
    ) -> "sqlite3.Connection":
        """Return a live connection holding the workbook's current state.

        Callers must hold ``self._session_lock(workbook_id)``. The connection
        is created on first use from the stored blob plus the durable
        operation log, and rebuilt whenever another writer promoted a newer
        blob, so it always reflects ``current_revision``.
        """
        row = self._db.fetch_one(
            "SELECT blob_revision, current_revision, blob_path, checksum "
            "FROM sum_workbooks WHERE id = ? AND deleted = 0",
            (int(workbook_id),),
        )
        if not row or not row.get("blob_path"):
            raise SumStorageError("Workbook file is not available")
        blob_revision = int(row.get("blob_revision") or 0)
        current_revision = int(row.get("current_revision") or 0)
        session = _WORKBOOK_SESSIONS.get(int(workbook_id))
        if session is not None:
            session.last_access_ms = int(time.time() * 1000)
            if session.loaded_blob_revision == blob_revision:
                return session.connection
            # Another writer promoted a newer blob; rebuild from it.
            SumManager._drop_session(int(workbook_id))
        data = self._blob_bytes(int(workbook_id), row)
        connection = self._open_session_connection(data)
        try:
            if current_revision > blob_revision:
                self._replay_unflushed(connection, int(workbook_id), blob_revision)
        except Exception:
            SumManager._close_session_connection(connection)
            raise
        session = _LiveSession(connection, blob_revision)
        session.pending = max(0, current_revision - blob_revision)
        with _SESSION_GUARD:
            _WORKBOOK_SESSIONS[int(workbook_id)] = session
        self._evict_sessions()
        # Refresh the cached index/calculation status once per materialization
        # (session-cached, so not per read). Never fail the connection over it.
        try:
            session = _WORKBOOK_SESSIONS.get(int(workbook_id))
            if session is None or session.pending > 0:
                self._refresh_workbook_status(connection, int(workbook_id))
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum workbook status refresh failed", exc_info=True)
        return connection

    def _refresh_workbook_status(
        self, connection: "sqlite3.Connection", workbook_id: int
    ) -> None:
        """Recompute index/calculation state from the live workbook file.

        ``index_state`` reflects the sum_indexes table (none / indexed /
        building); ``calculation_state`` is 'calculated' when any sheet has
        formula cells (the server evaluates them on read) and 'none'
        otherwise. Persisted to the main metadata row so list responses do not
        pay per-file I/O.
        """
        index_row = connection.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN state = 'ready' THEN 1 ELSE 0 END), 0) AS ready "
            "FROM sum_indexes"
        ).fetchone()
        total = int(index_row["total"] or 0) if index_row else 0
        ready = int(index_row["ready"] or 0) if index_row else 0
        if total == 0:
            index_state = "none"
        elif ready >= total:
            index_state = "indexed"
        else:
            index_state = "building"
        formula_row = connection.execute(
            "SELECT COUNT(*) AS count FROM sum_cells "
            "WHERE formula IS NOT NULL AND formula != ''"
        ).fetchone()
        formula_count = int(formula_row["count"] or 0) if formula_row else 0
        calculation_state = "calculated" if formula_count > 0 else "none"
        self._db.execute(
            "UPDATE sum_workbooks SET index_state = ?, calculation_state = ? "
            "WHERE id = ?",
            (index_state, calculation_state, int(workbook_id)),
        )

    def _blob_bytes(self, workbook_id: int, row) -> bytes:
        """Fetch and integrity-check the stored blob for a workbook row.

        The blob on disk is a PlexiSum container.  This method decodes it,
        verifies the SHA-256 checksum over the *uncompressed* payload, and
        caches the raw SQLite bytes so subsequent hot reads avoid the
        decompression + I/O cost.
        """
        cached = _WORKBOOK_FILE_CACHE.get(
            (int(workbook_id), int(row.get("blob_revision") or 0))
        )
        if cached is not None:
            if (
                row.get("checksum")
                and hashlib.sha256(cached).hexdigest() != row["checksum"]
            ):
                _WORKBOOK_FILE_CACHE.drop_workbook(int(workbook_id))
                raise SumStorageError("Workbook file checksum mismatch")
            return cached
        try:
            container_data = self._blob.retrieve(str(row["blob_path"]))
        except Exception as exc:
            raise SumStorageError("Workbook file could not be read") from exc
        if container_data[:4] == b"PXSM":
            from .container import decode_container

            db_bytes, _, _ = decode_container(container_data)
        else:
            db_bytes = container_data
        if (
            row.get("checksum")
            and hashlib.sha256(db_bytes).hexdigest() != row["checksum"]
        ):
            raise SumStorageError("Workbook file checksum mismatch")
        _WORKBOOK_FILE_CACHE.put(
            (int(workbook_id), int(row.get("blob_revision") or 0)), db_bytes
        )
        return db_bytes

    @staticmethod
    def _open_session_connection(data: bytes) -> "sqlite3.Connection":
        """Open workbook bytes as an in-memory connection usable across threads.

        Unlike the per-read openers this connection is long-lived, so it is
        created with ``check_same_thread=False`` and guarded by the
        per-workbook session lock. Runtimes without ``deserialize`` fall back
        to a private temp file whose path is registered for cleanup.
        """
        if data[:4] == b"PXSM":
            from .container import decode_container

            data, _, _ = decode_container(data)
        connection = None
        try:
            if getattr(sqlite3.Connection, "deserialize", None) is None:
                raise RuntimeError("deserialize unavailable")
            connection = sqlite3.connect(":memory:", check_same_thread=False)
            connection.deserialize(data)
        except Exception:
            # Close the :memory: connection that may have been created
            # before falling back to a temp-file-backed connection.
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            handle = tempfile.NamedTemporaryFile(
                prefix="plexisum-", suffix=".plexisum", delete=False
            )
            handle.write(data)
            handle.close()
            connection = sqlite3.connect(handle.name, check_same_thread=False)
            _SESSION_TMP_PATHS[id(connection)] = handle.name
        connection.row_factory = sqlite3.Row
        try:
            ensure_feature_tables(connection)
            format_row = connection.execute(
                "SELECT value FROM sum_meta WHERE key = 'format_version'"
            ).fetchone()
            if format_row is None or format_row["value"] != PLEXISUM_FORMAT_VERSION:
                raise SumStorageError("Unsupported PlexiSum workbook format")
        except (sqlite3.Error, SumStorageError) as exc:
            try:
                connection.close()
            except sqlite3.Error:
                pass
            if isinstance(exc, SumStorageError):
                raise
            raise SumStorageError("Workbook file schema could not be upgraded") from exc
        return connection

    @staticmethod
    def _serialize_connection(connection: "sqlite3.Connection") -> bytes:
        """Serialize an in-memory session to a PlexiSum container.

        The returned bytes are a complete container (header + compressed body +
        checksum) ready for storage.
        """
        from .container import encode_container

        try:
            if getattr(sqlite3.Connection, "serialize", None) is None:
                raise RuntimeError("serialize unavailable")
            raw = connection.serialize()
        except Exception:
            # Fallback for runtimes without serialize or WAL-mode sources.
            handle = tempfile.NamedTemporaryFile(
                prefix="plexisum-", suffix=".plexisum", delete=False
            )
            handle.close()
            try:
                connection.execute("VACUUM INTO ?", (handle.name,))
                with open(handle.name, "rb") as file_handle:
                    raw = file_handle.read()
            finally:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
        return encode_container(raw)

    def _replay_unflushed(
        self,
        connection: "sqlite3.Connection",
        workbook_id: int,
        after_revision: int,
    ) -> None:
        """Apply durable log rows the stored blob does not yet contain."""
        rows = self._db.fetch_all(
            "SELECT operation_id, actor_id, base_revision, result_revision, "
            "operation_type, sheet_id, payload_json, created_at "
            "FROM sum_operations WHERE workbook_id = ? AND result_revision > ? "
            "ORDER BY result_revision ASC",
            (int(workbook_id), int(after_revision)),
        )
        for row in rows:
            payload = self._decode(row["payload_json"], {})
            cells = payload.get("cells", [])
            revision = int(row["result_revision"])
            sheet_id = row.get("sheet_id") or payload.get("sheet_id")
            for cell in cells:
                try:
                    normalized = {
                        "row": int(cell["row"]),
                        "column": int(cell["column"]),
                        "value_type": str(cell.get("value_type", "blank")),
                        "value": cell.get("value"),
                        "formula": cell.get("formula"),
                        "style": cell.get("style"),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
                self._apply_normalized_cell(
                    connection,
                    str(sheet_id),
                    normalized,
                    revision,
                    int(row["created_at"]),
                )
            connection.execute(
                "INSERT INTO sum_operations (operation_id, actor_id, "
                "base_revision, result_revision, operation_type, payload_json, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["operation_id"],
                    row["actor_id"],
                    int(row["base_revision"]),
                    revision,
                    row["operation_type"],
                    self._json({"sheet_id": str(sheet_id), "cells": cells}),
                    int(row["created_at"]),
                ),
            )
        if rows:
            connection.commit()

    def _apply_normalized_cell(
        self,
        connection: "sqlite3.Connection",
        sheet_id: str,
        cell: Dict[str, Any],
        revision: int,
        now: int,
    ) -> None:
        """Upsert one normalized cell, honoring the __preserve__ sentinel."""
        row_index = cell["row"]
        column_index = cell["column"]
        if cell["value_type"] == "__preserve__":
            previous = connection.execute(
                "SELECT value_type, value_json, formula FROM sum_cells "
                "WHERE sheet_id = ? AND row_index = ? AND column_index = ?",
                (sheet_id, row_index, column_index),
            ).fetchone()
            if previous:
                value_type = previous["value_type"]
                value_json = previous["value_json"]
                formula = previous["formula"]
            else:
                value_type = "blank"
                value_json = self._json(None)
                formula = None
            connection.execute(
                "INSERT INTO sum_cells (sheet_id, row_index, column_index, "
                "value_type, value_json, formula, style_json, revision, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(sheet_id, row_index, column_index) DO UPDATE SET "
                "value_type = excluded.value_type, value_json = excluded.value_json, "
                "formula = excluded.formula, style_json = excluded.style_json, "
                "revision = excluded.revision, updated_at = excluded.updated_at",
                (
                    sheet_id,
                    row_index,
                    column_index,
                    value_type,
                    value_json,
                    formula,
                    self._json(cell["style"]),
                    revision,
                    now,
                ),
            )
            # Invalidate any segment covering this row
            self._invalidate_segment(connection, sheet_id, column_index, row_index)
            return
        connection.execute(
            "INSERT INTO sum_cells (sheet_id, row_index, column_index, "
            "value_type, value_json, formula, style_json, revision, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(sheet_id, row_index, column_index) DO UPDATE SET "
            "value_type = excluded.value_type, value_json = excluded.value_json, "
            "formula = excluded.formula, style_json = excluded.style_json, "
            "revision = excluded.revision, updated_at = excluded.updated_at",
            (
                sheet_id,
                row_index,
                column_index,
                cell["value_type"],
                self._json(cell["value"]),
                cell["formula"],
                self._json(cell["style"]),
                revision,
                now,
            ),
        )
        # Invalidate any segment covering this row
        self._invalidate_segment(connection, sheet_id, column_index, row_index)

    def _invalidate_segment(
        self,
        connection: "sqlite3.Connection",
        sheet_id: str,
        column_index: int,
        row_index: int,
    ) -> None:
        """Mark a column segment as stale so it gets rebuilt on next read."""
        try:
            connection.execute(
                "DELETE FROM sum_column_segments "
                "WHERE sheet_id = ? AND column_id = ? AND row_start <= ? AND row_end > ?",
                (sheet_id, str(column_index), row_index, row_index),
            )
        except sqlite3.Error:
            pass

    def _maybe_flush(self, workbook_id: int) -> None:
        """Promote the live copy when the coalescing window is exceeded.

        Called under the per-workbook session lock after each acknowledged
        operation. A flush failure never fails the operation itself (it is
        already durable); the session is dropped so the next access rebuilds
        from the log and retries the flush on a later write.
        """
        now = int(time.time() * 1000)
        session = _WORKBOOK_SESSIONS.get(int(workbook_id))
        if session is None:
            return
        if session.pending < SUM_FLUSH_MAX_OPS and (
            now - session.last_flush_ms < SUM_FLUSH_MAX_MS
        ):
            return
        try:
            self._flush_session_unlocked(int(workbook_id), session)
        except Exception:
            import utils.logger as logger

            logger.debug(
                "PlexiSum: coalesced flush failed for workbook %s",
                workbook_id,
                exc_info=True,
            )
            SumManager._drop_session(int(workbook_id))

    def _flush_session_unlocked(self, workbook_id: int, session: _LiveSession) -> None:
        """Store the live copy as a new immutable blob, CAS-gated on blob_revision.

        The CAS makes concurrent multi-worker flushes safe: exactly one writer
        promotes its snapshot; losers delete their blob and rebuild from the
        winner (their operations remain durable in the log). The log is
        compacted up to the flushed revision in the same transaction.
        """
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum revision writes require a standalone transaction"
            )
        now = int(time.time() * 1000)
        revision = self._current_revision(int(workbook_id))
        data = self._serialize_connection(session.connection)
        path, size, checksum = self._save_file(int(workbook_id), int(revision), data)
        try:
            self._db.begin_transaction()
            cursor = self._db.execute(
                "UPDATE sum_workbooks SET blob_revision = ?, blob_path = ?, "
                "file_size = ?, checksum = ?, updated_at = ? "
                "WHERE id = ? AND blob_revision = ?",
                (
                    int(revision),
                    path,
                    size,
                    checksum,
                    now,
                    int(workbook_id),
                    session.loaded_blob_revision,
                ),
            )
            try:
                affected = int(cursor.rowcount)
            finally:
                cursor.close()
            if affected != 1:
                raise SumConflictError("Workbook was flushed by another writer")
            self._db.execute(
                "INSERT INTO sum_revision_blobs "
                "(id, workbook_id, revision, blob_path, checksum, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._generate_id(),
                    int(workbook_id),
                    int(revision),
                    path,
                    checksum,
                    now,
                ),
            )
            self._db.execute(
                "DELETE FROM sum_operations WHERE workbook_id = ? "
                "AND result_revision <= ?",
                (int(workbook_id), int(revision)),
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            try:
                self._blob.delete(path)
            except Exception:
                pass
            raise
        self._cache_current_file(int(workbook_id), int(revision), data)
        session.loaded_blob_revision = int(revision)
        session.pending = 0
        session.last_flush_ms = now
        self._prune_revision_blobs(int(workbook_id))

    def _evict_sessions(self) -> None:
        """Drop the least recently used sessions beyond the entry cap.

        Only sessions whose lock can be acquired without blocking are
        evicted, so a session mid-operation is never closed underneath its
        holder; those are swept on a later eviction pass. Sessions with
        pending writes are flushed first (best-effort).
        """
        while len(_WORKBOOK_SESSIONS) > SUM_SESSION_MAX_ENTRIES:
            with _SESSION_GUARD:
                candidates = sorted(
                    _WORKBOOK_SESSIONS,
                    key=lambda wb: _WORKBOOK_SESSIONS[wb].last_access_ms,
                )
            victim = None
            victim_lock = None
            for candidate in candidates:
                lock = _WORKBOOK_SESSION_LOCKS.get(candidate)
                if lock is not None and not lock.acquire(blocking=False):
                    continue
                victim = candidate
                victim_lock = lock
                break
            if victim is None:
                # Every candidate is in use; stop rather than grow unbounded.
                return
            try:
                session = _WORKBOOK_SESSIONS.pop(victim, None)
                if session is None:
                    continue
                if session.pending > 0:
                    try:
                        self._flush_session_unlocked(victim, session)
                    except Exception:
                        import utils.logger as logger

                        logger.debug(
                            "PlexiSum: eviction flush failed for workbook %s",
                            victim,
                            exc_info=True,
                        )
                SumManager._close_session_connection(session.connection)
            finally:
                if victim_lock is not None:
                    victim_lock.release()

    def _current_revision(self, workbook_id: int) -> int:
        row = self._db.fetch_one(
            "SELECT current_revision FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        return int(row["current_revision"]) if row else 0

    def _append_op_durable(
        self,
        workbook_id: int,
        operation_id: str,
        user_id: int,
        base_revision: int,
        revision: int,
        operation_type: str,
        sheet_id: int,
        cells: List[Dict[str, Any]],
        now: int,
        conflict_message: str,
    ) -> None:
        """Durably record an operation and advance the revision counter.

        Both happen in one standalone main-database transaction, so a CAS
        failure records nothing and a crash before the in-memory apply is
        recovered by log replay on the next materialization. The caller must
        not be inside an outer main-database transaction.
        """
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum operation writes require a standalone transaction"
            )
        self._db.begin_transaction()
        try:
            cursor = self._db.execute(
                "UPDATE sum_workbooks SET current_revision = ?, updated_at = ? "
                "WHERE id = ? AND current_revision = ?",
                (
                    int(revision),
                    int(now),
                    int(workbook_id),
                    int(base_revision),
                ),
            )
            try:
                affected = int(cursor.rowcount)
            finally:
                cursor.close()
            if affected != 1:
                raise SumConflictError(conflict_message)
            self._db.execute(
                "INSERT INTO sum_operations (id, workbook_id, operation_id, "
                "actor_id, base_revision, result_revision, operation_type, "
                "sheet_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?)",
                (
                    self._generate_id(),
                    int(workbook_id),
                    str(operation_id),
                    int(user_id),
                    int(base_revision),
                    int(revision),
                    str(operation_type),
                    str(sheet_id),
                    self._json({"sheet_id": str(sheet_id), "cells": cells}),
                    int(now),
                ),
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            raise

    def _delete_blob_confirmed(self, path: str) -> bool:
        """Delete a blob and confirm that it is no longer present.

        Storage adapters return ``False`` both for an already-missing object
        and, in the media boundary, for a swallowed provider error. The
        explicit existence check distinguishes those cases sufficiently for
        retryable cleanup: an absent object is success, while an object that
        remains is failure and its metadata pointer must be retained.
        """
        if not path:
            return True
        try:
            self._blob.delete(path)
        except Exception:
            return False
        try:
            # Always verify, even when delete() reported success; some remote
            # adapters acknowledge a request before the object is gone.
            return not self._blob.exists(path)
        except Exception:
            return False

    def _prune_revision_blobs(self, workbook_id: int) -> None:
        """Best-effort GC of unreferenced old immutable revision blobs."""
        try:
            rows = self._db.fetch_all(
                "SELECT id, blob_path FROM sum_revision_blobs "
                "WHERE workbook_id = ? ORDER BY revision DESC, created_at DESC",
                (int(workbook_id),),
            )
        except Exception:
            import utils.logger as logger

            logger.error(
                f"PlexiSum: failed to read workbook {workbook_id} revision metadata",
                exc_info=True,
            )
            return
        # Keep the current blob plus REVISION_KEEP_COUNT of history: the
        # newest row in the table is the live workbook file and must never be
        # deleted, so the GC window starts after it.
        for row in rows[REVISION_KEEP_COUNT + 1 :]:
            path = str(row.get("blob_path") or "")
            try:
                if path:
                    if not self._delete_blob_confirmed(path):
                        raise SumStorageError(
                            f"Revision blob could not be deleted: {path}"
                        )
                self._db.execute(
                    "DELETE FROM sum_revision_blobs WHERE id = ?", (row["id"],)
                )
            except Exception:
                import utils.logger as logger

                logger.debug(
                    "PlexiSum: failed to delete superseded revision blob %s",
                    path,
                    exc_info=True,
                )

    def _save_file(
        self, workbook_id: int, revision: int, raw_bytes: bytes
    ) -> Tuple[str, int, str]:
        """Store a uniquely named immutable revision blob.

        *raw_bytes* must be raw (uncompressed) SQLite bytes.  They are
        wrapped in the PlexiSum container format before storage.  The
        returned checksum is the SHA-256 of the *uncompressed* payload.
        """
        from .container import encode_container

        container = encode_container(raw_bytes)
        key = f"{int(workbook_id)}/rev-{int(revision)}-{uuid.uuid4().hex}.plexisum"
        path = self._blob.store(container, key)
        return path, len(container), hashlib.sha256(raw_bytes).hexdigest()

    def _publish_revision(
        self,
        workbook_id: int,
        expected_revision: int,
        revision: int,
        data: bytes,
        now: int,
        conflict_message: str,
    ) -> Tuple[str, int, str]:
        """Publish one immutable file and atomically advance main metadata."""
        # A caller-owned outer transaction could roll back the metadata after
        # the blob has been stored, leaving an unreachable external object.
        # Revision publication therefore owns its main-DB transaction and must
        # not be nested inside another one.
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum revision writes require a standalone transaction"
            )
        path, size, checksum = self._save_file(workbook_id, revision, data)
        try:
            self._db.begin_transaction()
            cursor = self._db.execute(
                "UPDATE sum_workbooks SET current_revision = ?, blob_revision = ?, "
                "blob_path = ?, file_size = ?, checksum = ?, updated_at = ? "
                "WHERE id = ? AND current_revision = ?",
                (
                    int(revision),
                    int(revision),
                    path,
                    size,
                    checksum,
                    now,
                    int(workbook_id),
                    int(expected_revision),
                ),
            )
            try:
                affected = int(cursor.rowcount)
            finally:
                cursor.close()
            if affected != 1:
                raise SumConflictError(conflict_message)
            self._db.execute(
                "INSERT INTO sum_revision_blobs "
                "(id, workbook_id, revision, blob_path, checksum, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._generate_id(),
                    int(workbook_id),
                    int(revision),
                    path,
                    checksum,
                    now,
                ),
            )
            # A full-file rewrite supersedes every buffered operation: the new
            # blob is complete at ``revision``, so the durable log can be
            # compacted up to it in the same transaction.
            self._db.execute(
                "DELETE FROM sum_operations WHERE workbook_id = ? "
                "AND result_revision <= ?",
                (int(workbook_id), int(revision)),
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            self._blob.delete(path)
            raise
        self._cache_current_file(workbook_id, revision, data)
        # The new blob is authoritative; drop any live session so the next
        # access rebuilds from it (its content may lack this rewrite).
        with self._session_lock(int(workbook_id)):
            self._drop_session(int(workbook_id))
        self._prune_revision_blobs(workbook_id)
        return path, size, checksum

    @staticmethod
    def _open_sqlite(data: bytes) -> Tuple[sqlite3.Connection, str]:
        """Open workbook bytes as a sqlite connection over a temp file.

        Feature tables (validations, conditional formats, comments, freeze)
        are backfilled idempotently on every open so older ``.plexisum``
        files created before those tables shipped work transparently.
        """
        if data[:4] == b"PXSM":
            from .container import decode_container

            data, _, _ = decode_container(data)
        handle = tempfile.NamedTemporaryFile(
            prefix="plexisum-", suffix=".plexisum", delete=False
        )
        handle.write(data)
        handle.close()
        connection = sqlite3.connect(handle.name)
        connection.row_factory = sqlite3.Row
        try:
            ensure_feature_tables(connection)
            format_row = connection.execute(
                "SELECT value FROM sum_meta WHERE key = 'format_version'"
            ).fetchone()
            if format_row is None or format_row["value"] != PLEXISUM_FORMAT_VERSION:
                raise SumStorageError("Unsupported PlexiSum workbook format")
        except (sqlite3.Error, SumStorageError) as exc:
            SumManager._close_sqlite(connection, handle.name)
            if isinstance(exc, SumStorageError):
                raise
            raise SumStorageError("Workbook file schema could not be upgraded") from exc
        return connection, handle.name

    @staticmethod
    def _close_sqlite(connection: sqlite3.Connection, path: str) -> None:
        try:
            connection.close()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    @staticmethod
    def _open_sqlite_memory(data: bytes) -> Tuple[sqlite3.Connection, Optional[str]]:
        """Open workbook bytes as an in-memory sqlite connection.

        Hot read paths (viewport, sheet listings) used to write every blob to
        a temp file and unlink it on every call. ``Connection.deserialize``
        (Python >= 3.11) loads the bytes straight into ``:memory:``, avoiding
        the temp-file I/O entirely. Runtimes without ``deserialize`` fall
        back to the file-backed opener; callers branch on the returned path.
        """
        if data[:4] == b"PXSM":
            from .container import decode_container

            data, _, _ = decode_container(data)
        connection = None
        try:
            if getattr(sqlite3.Connection, "deserialize", None) is None:
                raise RuntimeError("deserialize unavailable")
            connection = sqlite3.connect(":memory:")
            connection.deserialize(data)
        except Exception:
            # Close the :memory: connection that may have been created
            # before falling back to a temp-file-backed connection.
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            return SumManager._open_sqlite(data)
        connection.row_factory = sqlite3.Row
        try:
            ensure_feature_tables(connection)
            format_row = connection.execute(
                "SELECT value FROM sum_meta WHERE key = 'format_version'"
            ).fetchone()
            if format_row is None or format_row["value"] != PLEXISUM_FORMAT_VERSION:
                raise SumStorageError("Unsupported PlexiSum workbook format")
        except (sqlite3.Error, SumStorageError) as exc:
            try:
                connection.close()
            except sqlite3.Error:
                pass
            if isinstance(exc, SumStorageError):
                raise
            raise SumStorageError("Workbook file schema could not be upgraded") from exc
        return connection, None

    @staticmethod
    def _close_sqlite_memory(connection: sqlite3.Connection) -> None:
        try:
            connection.close()
        except sqlite3.Error:
            pass

    def _read_sheets_from_file(self, data: bytes) -> List[Dict[str, Any]]:
        connection, path = self._open_sqlite_memory(data)
        try:
            rows = connection.execute(
                "SELECT id, name, position, created_at, updated_at FROM sum_sheets ORDER BY position, id"
            ).fetchall()
            return [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "position": int(row["position"]),
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                }
                for row in rows
            ]
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file schema is invalid") from exc
        finally:
            if path is None:
                self._close_sqlite_memory(connection)
            else:
                self._close_sqlite(connection, path)

    def _ensure_sheet(self, data: bytes, sheet_id: str) -> None:
        connection, path = self._open_sqlite(data)
        try:
            row = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
        finally:
            self._close_sqlite(connection, path)
        if row is None:
            raise SumNotFoundError("Sheet not found")

    def _resolver_for(
        self, connection: sqlite3.Connection
    ) -> Callable[[Optional[str], int, int], Optional[Dict[str, Any]]]:
        """Build a formula resolver backed by the open workbook connection.

        Formula references use sheet *names* (``Sheet2!A1``); the resolver
        maps names to the sheet ids stored in the file, memoizes lookups so
        repeated references do not re-query, and returns ``None`` for empty
        cells (evaluated as blank in formulas).
        """
        sheet_map: Dict[str, str] = {}
        try:
            for row in connection.execute(
                "SELECT id, name FROM sum_sheets WHERE deleted = 0"
            ).fetchall():
                sheet_map[str(row["name"])] = str(row["id"])
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(f"PlexiSum formula resolver could not load sheet names: {exc}")
        cache: Dict[Tuple[str, int, int], Optional[Dict[str, Any]]] = {}

        def resolver(
            sheet: Optional[str], row: int, column: int
        ) -> Optional[Dict[str, Any]]:
            sheet_id = sheet_map.get(str(sheet), str(sheet)) if sheet else ""
            key = (sheet_id, int(row), int(column))
            if key in cache:
                return cache[key]
            row_data = connection.execute(
                "SELECT value_type, value_json, formula FROM sum_cells "
                "WHERE sheet_id = ? AND row_index = ? AND column_index = ?",
                (sheet_id, int(row), int(column)),
            ).fetchone()
            if row_data is None:
                cache[key] = None
                return None
            result: Dict[str, Any] = {
                "value_type": row_data["value_type"],
                "value": self._decode(row_data["value_json"]),
                "formula": row_data["formula"],
            }
            cache[key] = result
            return result

        return resolver

    # ── workbook CRUD ────────────────────────────────────────────────────────

    def create_workbook(
        self,
        user_id: int,
        title: str,
        scope: str = "personal",
        server_id: Optional[int] = None,
        template: bool = False,
    ) -> Dict[str, Any]:
        """Create an empty workbook (or a reusable template when ``template``)."""
        title = str(title or "").strip()
        scope = str(scope or "personal").strip().lower()
        if not title or len(title) > 500:
            raise SumValidationError("title must be between 1 and 500 characters")
        if scope not in ("personal", "server"):
            raise SumValidationError("scope must be personal or server")
        if scope == "server" and server_id is None:
            raise SumValidationError("server_id is required for server workbooks")
        if scope == "personal" and server_id is not None:
            raise SumValidationError("personal workbooks cannot include server_id")
        if server_id is not None and not self._is_member(user_id, int(server_id)):
            raise SumPermissionError("You are not a member of this server")
        now = int(time.time() * 1000)
        workbook_id = self._generate_id()
        sheet_id = self._generate_id()

        handle = tempfile.NamedTemporaryFile(
            prefix="plexisum-new-", suffix=".plexisum", delete=False
        )
        handle.close()
        try:
            from .container import decode_container
            from .schema import create_workbook_file

            create_workbook_file(handle.name, workbook_id=workbook_id)
            with open(handle.name, "rb") as fh:
                container_data = fh.read()
            db_bytes, _, _ = decode_container(container_data)
            raw_handle = tempfile.NamedTemporaryFile(
                prefix="plexisum-raw-", suffix=".sqlite", delete=False
            )
            raw_handle.write(db_bytes)
            raw_handle.close()
            try:
                connection = sqlite3.connect(raw_handle.name)
                try:
                    connection.execute(
                        "INSERT INTO sum_sheets (id, name, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (str(sheet_id), "Sheet 1", 0, now, now),
                    )
                    connection.commit()
                finally:
                    connection.close()
                with open(raw_handle.name, "rb") as file_handle:
                    raw_data = file_handle.read()
            finally:
                try:
                    os.unlink(raw_handle.name)
                except OSError:
                    pass
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
        return self._publish_workbook(
            user_id,
            title,
            scope,
            server_id,
            raw_data,
            0,
            template=template,
            workbook_id=workbook_id,
        )

    def create_from_template(
        self,
        user_id: int,
        template_id: int,
        title: str,
        scope: str = "personal",
        server_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Instantiate a copy of a template workbook into a new workbook.

        The template must be visible to ``user_id`` (owner, server member, or
        live grant). The copy reuses the template's materialized file bytes,
        keeps its revision (the blob already carries the cells), and starts
        with an empty operation log so edits to the copy never affect the
        template.
        """
        template = self._workbook(user_id, template_id)
        if not int(template.get("is_template") or 0):
            raise SumValidationError("Source workbook is not a template")
        title = str(title or "").strip()
        if not title or len(title) > 500:
            raise SumValidationError("title must be between 1 and 500 characters")
        scope = str(scope or "personal").strip().lower()
        if scope not in ("personal", "server"):
            raise SumValidationError("scope must be personal or server")
        if scope == "server" and server_id is None:
            raise SumValidationError("server_id is required for server workbooks")
        if scope == "personal" and server_id is not None:
            raise SumValidationError("personal workbooks cannot include server_id")
        if server_id is not None and not self._is_member(user_id, int(server_id)):
            raise SumPermissionError("You are not a member of this server")
        # Materialize the template's current file bytes (operations log is
        # replayed into the snapshot, so the copy starts fully materialized).
        data = self._file_bytes(int(template_id))
        revision = int(template.get("current_revision") or 0)
        return self._publish_workbook(
            user_id, title, scope, server_id, data, revision, template=False
        )

    def _publish_workbook(
        self,
        user_id: int,
        title: str,
        scope: str,
        server_id: Optional[int],
        data: bytes,
        revision: int,
        template: bool = False,
        workbook_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Shared workbook publication: blob save + artifact + metadata row.

        Used by fresh creation and template instantiation so both paths keep
        the same transactional guarantees (artifact identity and workbook
        metadata commit together; the file is stored outside the main DB).
        """
        # As with revision publication, reject a caller-owned outer
        # transaction before creating an external blob. Otherwise that outer
        # rollback could discard the metadata while the file remains stored.
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum workbook creation requires a standalone transaction"
            )
        workbook_id = workbook_id if workbook_id is not None else self._generate_id()
        now = int(time.time() * 1000)
        path, size, checksum = self._save_file(workbook_id, int(revision), data)
        artifact_id = None
        transaction_started = False
        try:
            # Keep the artifact identity and workbook metadata in one main-DB
            # transaction. Workbook cells remain exclusively in the blob.
            self._db.begin_transaction()
            transaction_started = True
            from src.core.artifacts.manager import ArtifactManager
            from src.core.artifacts.models import ArtifactType

            artifact = ArtifactManager(self._db).create(
                conversation_id=None,
                author_id=user_id,
                artifact_type=ArtifactType.SUM,
                title=title,
                server_id=server_id,
                payload={"workbook_id": str(workbook_id)},
                emit_event=False,
            )
            artifact_id = int(artifact.id)
            self._db.execute(
                "INSERT INTO sum_workbooks (id, artifact_id, owner_id, title, scope, server_id, current_revision, blob_revision, blob_path, file_size, checksum, is_template, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workbook_id,
                    artifact_id,
                    user_id,
                    title,
                    scope,
                    server_id,
                    int(revision),
                    int(revision),
                    path,
                    size,
                    checksum,
                    1 if template else 0,
                    now,
                    now,
                ),
            )
            self._db.execute(
                "INSERT INTO sum_revision_blobs "
                "(id, workbook_id, revision, blob_path, checksum, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self._generate_id(), workbook_id, int(revision), path, checksum, now),
            )
            self._audit(
                "create",
                "workbook",
                workbook_id,
                user_id,
                {
                    "title": title,
                    "scope": scope,
                    "artifact_id": artifact_id,
                    "template": bool(template),
                },
            )
            self._db.commit()
            transaction_started = False
        except Exception:
            if transaction_started or getattr(self._db, "in_transaction", False):
                try:
                    self._db.rollback()
                except Exception:
                    pass
            self._blob.delete(path)
            # The rollback above normally removes the artifact too. This
            # compensation handles adapters that commit artifact writes
            # independently, without masking the original error.
            if artifact_id is not None:
                try:
                    from src.core.artifacts.repository import delete_artifact

                    delete_artifact(self._db, artifact_id)
                except Exception:
                    pass
            raise
        self._cache_current_file(workbook_id, int(revision), data)
        # The artifact event must follow the commit; otherwise a failed
        # transaction can announce an identity that was rolled back.
        try:
            if artifact_id is not None:
                from src.core.artifacts.manager import ArtifactManager

                committed_artifact = ArtifactManager(self._db).get(int(artifact_id))
                if committed_artifact is not None:
                    ArtifactManager(self._db)._emit_lifecycle_event(
                        "create", committed_artifact
                    )
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum artifact create event failed", exc_info=True)
        return self.get_workbook(user_id, workbook_id)

    def set_pinned(
        self, user_id: int, workbook_id: int, pinned: bool
    ) -> Dict[str, Any]:
        """Pin (or unpin) a workbook for its owner's home surface."""
        row = self._workbook(user_id, workbook_id)
        viewer = self._viewer_permissions(user_id, int(workbook_id), workbook=row)
        if not viewer.get("can_edit"):
            raise SumPermissionError("You do not have edit access to this workbook")
        # Pinning is a home-surface preference, not content activity: it must
        # not bump updated_at (which drives the recent-workbooks ordering).
        self._db.execute(
            "UPDATE sum_workbooks SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, int(workbook_id)),
        )
        return self.get_workbook(user_id, workbook_id)

    def set_template(
        self, user_id: int, workbook_id: int, is_template: bool
    ) -> Dict[str, Any]:
        """Mark (or unmark) a workbook as a reusable template.

        Only an editor may toggle the flag. Templates are server-flagged
        workbooks listed by ``list_workbooks(template=True)`` and instantiated
        through ``create_from_template``.
        """
        row = self._workbook(user_id, workbook_id)
        viewer = self._viewer_permissions(user_id, int(workbook_id), workbook=row)
        if not viewer.get("can_edit"):
            raise SumPermissionError("You do not have edit access to this workbook")
        now = int(time.time() * 1000)
        self._db.execute(
            "UPDATE sum_workbooks SET is_template = ?, updated_at = ? WHERE id = ?",
            (1 if is_template else 0, now, int(workbook_id)),
        )
        self._audit(
            "set_template" if is_template else "unset_template",
            "workbook",
            int(workbook_id),
            user_id,
        )
        return self.get_workbook(user_id, workbook_id)

    def get_workbook(self, user_id: int, workbook_id: int) -> Dict[str, Any]:
        row = self._workbook(user_id, workbook_id)
        sheets = self._read_sheets_from_file(self._file_bytes(workbook_id))
        return {
            "id": str(row["id"]),
            "artifact_id": str(row["artifact_id"])
            if row.get("artifact_id") is not None
            else None,
            "owner_id": str(row["owner_id"]),
            "title": row["title"],
            "scope": row["scope"],
            "server_id": str(row["server_id"])
            if row.get("server_id") is not None
            else None,
            "current_revision": int(row["current_revision"]),
            "sheets": sheets,
            "pinned": bool(int(row.get("pinned") or 0)),
            "is_template": bool(int(row.get("is_template") or 0)),
            "index_state": str(row.get("index_state") or "none"),
            "calculation_state": str(row.get("calculation_state") or "none"),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "my_permissions": self._viewer_permissions(
                user_id, int(row["id"]), workbook=row
            ),
        }

    def list_workbooks(
        self,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        template: bool = False,
        search: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        # In addition to owned and server-scope workbooks, include workbooks
        # shared with this user through a live grant (view or edit).
        now = int(time.time() * 1000)
        where = (
            "deleted = 0 AND is_template = ? AND (owner_id = ? OR "
            "(scope = 'server' AND server_id IN "
            "(SELECT server_id FROM srv_members WHERE user_id = ?)) OR "
            "EXISTS (SELECT 1 FROM sum_grants g "
            "WHERE g.workbook_id = sum_workbooks.id AND g.subject_id = ? "
            "AND g.revoked_at IS NULL "
            "AND (g.expires_at IS NULL OR g.expires_at > ?)))"
        )
        params: list = [1 if template else 0, user_id, user_id, user_id, now]
        if scope is not None:
            normalized_scope = str(scope).strip().lower()
            if normalized_scope not in ("personal", "server"):
                raise SumValidationError("scope must be personal or server")
            where += " AND scope = ?"
            params.append(normalized_scope)
        if search:
            needle = f"%{str(search).strip().lower()}%"
            where += " AND LOWER(title) LIKE ?"
            params.append(needle)
        count = self._db.fetch_one(
            f"SELECT COUNT(*) AS count FROM sum_workbooks WHERE {where}", tuple(params)
        )
        # Pinned workbooks float to the top of the home surface; templates are
        # newest-first within their own listing.
        order = "pinned DESC, updated_at DESC" if not template else "updated_at DESC"
        rows = self._db.fetch_all(
            f"SELECT * FROM sum_workbooks WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        )
        items = []
        for row in rows:
            items.append(
                {
                    "id": str(row["id"]),
                    "artifact_id": str(row["artifact_id"])
                    if row.get("artifact_id") is not None
                    else None,
                    "owner_id": str(row["owner_id"]),
                    "title": row["title"],
                    "scope": row["scope"],
                    "server_id": str(row["server_id"])
                    if row.get("server_id") is not None
                    else None,
                    "current_revision": int(row["current_revision"]),
                    "pinned": bool(int(row.get("pinned") or 0)),
                    "is_template": bool(int(row.get("is_template") or 0)),
                    "index_state": str(row.get("index_state") or "none"),
                    "calculation_state": str(row.get("calculation_state") or "none"),
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                }
            )
        return items, int(count["count"]) if count else 0

    def delete_workbook(self, user_id: int, workbook_id: int) -> None:
        workbook = self._workbook(user_id, workbook_id, mutation=True)
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum lifecycle writes require a standalone transaction"
            )
        now = int(time.time() * 1000)
        artifact_id = workbook.get("artifact_id")
        self._db.begin_transaction()
        try:
            self._db.execute(
                "UPDATE sum_workbooks SET deleted = 1, updated_at = ? WHERE id = ?",
                (now, int(workbook_id)),
            )
            # The artifact is the platform-owned lifecycle identity for the
            # workbook. Archive it with the metadata row so universal-app and
            # artifact queries do not expose a deleted workbook as live.
            if artifact_id is not None:
                from src.core.artifacts.manager import ArtifactManager
                from src.core.artifacts.models import ArtifactStatus

                ArtifactManager(self._db).update(
                    int(artifact_id), status=ArtifactStatus.ARCHIVED, emit_event=False
                )
            self._audit("delete", "workbook", int(workbook_id), user_id)
            # Purge only buffered operations that are already materialized in
            # the stored blob; unflushed rows must survive so a restore can
            # replay them.
            self._db.execute(
                "DELETE FROM sum_operations WHERE workbook_id = ? "
                "AND result_revision <= (SELECT blob_revision FROM "
                "sum_workbooks WHERE id = ?)",
                (int(workbook_id), int(workbook_id)),
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            raise
        # Drop any live session; its log rows are gone, so a stale in-memory
        # copy must never be reused after a restore.
        with self._session_lock(int(workbook_id)):
            self._drop_session(int(workbook_id))
        # Emit only after both workbook and artifact state are committed.
        if artifact_id is not None:
            try:
                from src.core.artifacts.manager import ArtifactManager

                committed_artifact = ArtifactManager(self._db).get(int(artifact_id))
                if committed_artifact is not None:
                    ArtifactManager(self._db)._emit_lifecycle_event(
                        "update", committed_artifact
                    )
            except Exception:
                import utils.logger as logger

                logger.debug("PlexiSum artifact archive event failed", exc_info=True)

    def restore_workbook(self, user_id: int, workbook_id: int) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM sum_workbooks WHERE id = ? AND deleted = 1",
            (int(workbook_id),),
        )
        if not row:
            raise SumNotFoundError("Deleted workbook not found")
        self._authorize(row, user_id, mutation=True)
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum lifecycle writes require a standalone transaction"
            )
        now = int(time.time() * 1000)
        artifact_id = row.get("artifact_id")
        self._db.begin_transaction()
        try:
            self._db.execute(
                "UPDATE sum_workbooks SET deleted = 0, updated_at = ? WHERE id = ?",
                (now, int(workbook_id)),
            )
            if artifact_id is not None:
                from src.core.artifacts.manager import ArtifactManager
                from src.core.artifacts.models import ArtifactStatus

                ArtifactManager(self._db).update(
                    int(artifact_id), status=ArtifactStatus.COMPLETED, emit_event=False
                )
            self._audit("restore", "workbook", int(workbook_id), user_id)
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            raise
        with self._session_lock(int(workbook_id)):
            self._drop_session(int(workbook_id))
        # Emit only after both workbook and artifact state are committed.
        if artifact_id is not None:
            try:
                from src.core.artifacts.manager import ArtifactManager

                committed_artifact = ArtifactManager(self._db).get(int(artifact_id))
                if committed_artifact is not None:
                    ArtifactManager(self._db)._emit_lifecycle_event(
                        "update", committed_artifact
                    )
            except Exception:
                import utils.logger as logger

                logger.debug("PlexiSum artifact restore event failed", exc_info=True)
        return self.get_workbook(user_id, workbook_id)

    # ── file download / upload ───────────────────────────────────────────────

    def download_file(self, user_id: int, workbook_id: int) -> Dict[str, Any]:
        """Return the current workbook file for authorized download.

        The payload is the RAW SQLite bytes (base64-encoded), not the PXSM
        storage container: ``upload_file`` accepts both forms, and the
        download/upload round-trip must hand back a file that opens directly
        as a SQLite database (the previous double-wrapped container could
        not).
        """
        row = self._workbook(user_id, workbook_id)
        raw_data = self._file_bytes(workbook_id)
        if raw_data[:4] == b"PXSM":
            from .container import decode_container

            db_bytes, _, _ = decode_container(raw_data)
        else:
            db_bytes = raw_data
        self._audit(
            "download",
            "workbook_file",
            int(workbook_id),
            user_id,
            {"size": len(db_bytes)},
        )
        return {
            "workbook_id": str(workbook_id),
            "filename": f"{row['title'] or 'workbook'}.plexisum",
            "content": base64.b64encode(db_bytes).decode("ascii"),
            "size": len(db_bytes),
        }

    def upload_file(
        self,
        user_id: int,
        workbook_id: int,
        data: bytes,
        base_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Replace the workbook file after validating it is a .plexisum file."""
        self._workbook(user_id, workbook_id, mutation=True)
        if not isinstance(data, bytes) or not data:
            raise SumValidationError("Workbook file is required")
        if len(data) > 50 * 1024 * 1024:
            raise SumValidationError("Workbook file is too large")
        self._validate_workbook_file(data)
        if data[:4] == b"PXSM":
            from .container import decode_container

            db_bytes, _, _ = decode_container(data)
        else:
            db_bytes = data
        current = self._db.fetch_one(
            "SELECT current_revision FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        base = current["current_revision"] if current else 0
        if base_revision is not None and int(base_revision) != int(base):
            raise SumConflictError(f"Workbook revision is {base}")
        revision = int(base) + 1
        now = int(time.time() * 1000)
        path, size, checksum = self._publish_revision(
            int(workbook_id),
            int(base),
            int(revision),
            db_bytes,
            now,
            "Workbook revision changed during upload",
        )
        self._audit(
            "upload",
            "workbook_file",
            int(workbook_id),
            user_id,
            {"revision": revision, "size": size, "checksum": checksum[:16]},
        )
        try:
            row = self._db.fetch_one(
                "SELECT * FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
            )
            if row:
                self._emit_update(row, revision, user_id)
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum update emit failed", exc_info=True)
        return {
            "workbook_id": str(workbook_id),
            "revision": revision,
            "size": size,
            "checksum": checksum,
        }

    def _validate_workbook_file(self, data: bytes) -> None:
        """Validate format, integrity, and resource bounds before storage."""
        from .container import decode_container

        max_bytes = 50 * 1024 * 1024
        if not isinstance(data, bytes) or not data:
            raise SumValidationError("Workbook file is required")
        if len(data) > max_bytes:
            raise SumValidationError("Workbook file is too large")
        if data[:4] == b"PXSM":
            try:
                db_bytes, _, _ = decode_container(data)
            except (ValueError, RuntimeError) as exc:
                raise SumValidationError(
                    "File is not a valid PlexiSum container"
                ) from exc
            if len(db_bytes) > max_bytes:
                raise SumValidationError("Workbook file is too large")
            if not db_bytes.startswith(b"SQLite format 3"):
                raise SumValidationError(
                    "Container does not contain a valid SQLite database"
                )
            data = db_bytes
        elif not data.startswith(b"SQLite format 3"):
            raise SumValidationError(
                "File is not a PlexiSum container or SQLite database"
            )
        try:
            connection, path = self._open_sqlite(data)
        except SumStorageError as exc:
            raise SumValidationError(
                "File is not a supported PlexiSum workbook"
            ) from exc
        try:
            format_row = connection.execute(
                "SELECT value FROM sum_meta WHERE key = 'format_version'"
            ).fetchone()
            if format_row is None or format_row["value"] != PLEXISUM_FORMAT_VERSION:
                raise SumValidationError("File is not a supported PlexiSum workbook")
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            if page_size <= 0 or page_count <= 0 or page_size * page_count > max_bytes:
                raise SumValidationError("Workbook SQLite allocation is too large")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if str(integrity).lower() != "ok":
                raise SumValidationError("Workbook SQLite integrity check failed")
            object_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index', 'trigger', 'view')"
                ).fetchone()[0]
            )
            if object_count > 1_000:
                raise SumValidationError("Workbook contains too many SQLite objects")
            sheet_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sum_sheets WHERE deleted = 0"
                ).fetchone()[0]
            )
            cell_count = int(
                connection.execute("SELECT COUNT(*) FROM sum_cells").fetchone()[0]
            )
            if sheet_count > 1_000 or cell_count > 5_000_000:
                raise SumValidationError("Workbook dimensions exceed supported limits")
            extent = connection.execute(
                "SELECT MAX(row_index), MAX(column_index) FROM sum_cells"
            ).fetchone()
            if extent[0] is not None and int(extent[0]) > 10_000_000:
                raise SumValidationError("Workbook row extent exceeds supported limits")
            if extent[1] is not None and int(extent[1]) > 16_384:
                raise SumValidationError(
                    "Workbook column extent exceeds supported limits"
                )
        except SumValidationError:
            raise
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise SumValidationError("File is not a valid PlexiSum workbook") from exc
        finally:
            self._close_sqlite(connection, path)

    # ── viewport reads ───────────────────────────────────────────────────────

    # ── viewport reads ───────────────────────────────────────────────────────

    # ── solo-mode helpers ─────────────────────────────────────────────────

    def _is_solo(self, workbook_id: int) -> bool:
        """Check if anyone else is subscribed to this workbook."""
        try:
            from src.api.websocket.sum import get_sum_subscription_registry

            registry = get_sum_subscription_registry()
            subscribers = registry.get_subscribers(int(workbook_id))
            return len(subscribers) <= 1
        except Exception:
            return True  # If we can't check, treat as solo

    def _read_viewport_solo(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        filters: Optional[List[Dict[str, Any]]],
        workbook: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Read viewport directly from the stored blob (no in-memory session).

        Opens the blob through ``_open_sqlite_memory`` (deserialize straight
        into ``:memory:``) instead of writing it to a temp file and reopening
        it on every poll. That removed the per-read disk write + reopen cost
        that solo/read-only viewers paid on each scroll. The caller passes the
        already-authorized workbook row so this path also skips a redundant
        metadata lookup.
        """
        if workbook is None:
            workbook = self._workbook(user_id, workbook_id)
        data = self._blob_bytes(int(workbook_id), workbook)
        connection, path = self._open_sqlite_memory(data)
        try:
            conn = connection

            if (row_end - row_start) * (
                column_end - column_start
            ) > self.MAX_VIEWPORT_CELLS:
                raise SumValidationError("Viewport is too large")

            sheet = conn.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")

            rows = conn.execute(
                "SELECT row_index, column_index, value_type, value_json, formula, "
                "style_json, revision "
                "FROM sum_cells WHERE sheet_id = ? AND row_index >= ? AND row_index < ? "
                "AND column_index >= ? AND column_index < ? ORDER BY row_index, column_index",
                (str(sheet_id), row_start, row_end, column_start, column_end),
            ).fetchall()

            used = conn.execute(
                "SELECT MAX(row_index) AS max_row, MAX(column_index) AS max_col "
                "FROM sum_cells WHERE sheet_id = ?",
                (str(sheet_id),),
            ).fetchone()

            cells = []
            formula_cells = []
            for row in rows:
                cell_data = {
                    "row": int(row["row_index"]),
                    "column": int(row["column_index"]),
                    "value_type": row["value_type"],
                    "value": self._decode(row["value_json"]),
                    "formula": row["formula"],
                    "style": self._decode(row["style_json"]),
                    "revision": int(row["revision"]),
                }
                if cell_data["value_type"] == "formula" and cell_data["formula"]:
                    formula_cells.append(
                        (cell_data["row"], cell_data["column"], cell_data)
                    )
                cells.append(cell_data)

            if formula_cells:
                from .formulas import FormulaEvaluator

                resolver = self._resolver_for(conn)
                evaluator = FormulaEvaluator(resolver)
                for row_index, column_index, formula_cell in formula_cells:
                    formula_cell["computed"] = evaluator.evaluate_cell(
                        str(sheet_id),
                        row_index,
                        column_index,
                        formula_cell["formula"],
                    )

            max_row = (
                int(used["max_row"]) + 1 if used and used["max_row"] is not None else 0
            )
            max_col = (
                int(used["max_col"]) + 1 if used and used["max_col"] is not None else 0
            )

            hidden_rows = []
            if filters:
                by_row = {}
                for cell in cells:
                    by_row.setdefault(int(cell["row"]), []).append(cell)
                visible = {}
                for row_index in sorted(by_row):
                    row_cells = by_row[row_index]
                    matches = True
                    for criterion in filters:
                        column_index = int(criterion["column"])
                        cell = next(
                            (c for c in row_cells if int(c["column"]) == column_index),
                            None,
                        )
                        value = self._filter_cell_value(cell)
                        if not self._matches_filter(
                            value, criterion["operator"], criterion.get("value")
                        ):
                            matches = False
                            break
                    visible[row_index] = matches
                hidden_rows = [r for r in sorted(by_row) if not visible[r]]

            metadata = self._read_viewport_metadata(
                conn, str(sheet_id), row_start, row_end, column_start, column_end
            )
            viewer = self._viewer_permissions(
                user_id,
                int(workbook_id),
                sheet_id=int(sheet_id),
                workbook=workbook,
            )

            return {
                "workbook_id": str(workbook_id),
                "sheet_id": str(sheet_id),
                "revision": int(workbook["current_revision"]),
                "row_start": row_start,
                "row_end": row_end,
                "column_start": column_start,
                "column_end": column_end,
                "row_count": max_row,
                "column_count": max_col,
                "has_more": bool(row_end < max_row or column_end < max_col),
                "cells": cells,
                "hidden_rows": hidden_rows,
                "filters": filters or [],
                "freeze": metadata["freeze"],
                "validations": metadata["validations"],
                "conditional_formats": metadata["conditional_formats"],
                "comments": metadata["comments"],
                "can_edit": bool(viewer["can_edit"]),
                "read_only_ranges": viewer["read_only_ranges"],
            }
        finally:
            if path is None:
                self._close_sqlite_memory(connection)
            else:
                self._close_sqlite(connection, path)

    @staticmethod
    def _filter_cell_value(cell: Optional[Dict[str, Any]]) -> Any:
        """The comparable value for filter evaluation."""
        if not cell:
            return None
        if cell.get("value_type") == "formula":
            computed = cell.get("computed")
            if is_error(computed):
                return None
            return computed
        return cell.get("value")

    @staticmethod
    def _matches_filter(value: Any, operator: str, target: Any = None) -> bool:
        """Evaluate one filter criterion against a cell's comparable value."""
        op = str(operator or "").strip()
        if op == "is_empty":
            return value is None or value == ""
        if op == "is_not_empty":
            return not (value is None or value == "")
        if op == "contains":
            return value is not None and str(target or "").lower() in str(value).lower()
        if op == "not_contains":
            return not (
                value is not None and str(target or "").lower() in str(value).lower()
            )
        if value is None:
            return False
        if op == "eq":
            if isinstance(value, (int, float)) and isinstance(target, (int, float)):
                return float(value) == float(target)
            return str(value).lower() == str(target).lower()
        if op == "neq":
            if isinstance(value, (int, float)) and isinstance(target, (int, float)):
                return float(value) != float(target)
            return str(value).lower() != str(target).lower()
        try:
            number = float(value)
            target_number = float(target)
        except (TypeError, ValueError):
            return False
        if op == "gt":
            return number > target_number
        if op == "gte":
            return number >= target_number
        if op == "lt":
            return number < target_number
        if op == "lte":
            return number <= target_number
        return True

    def _read_viewport_metadata(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        max_comments: int = 200,
    ) -> Dict[str, Any]:
        """Freeze config, validations, conditional formats, and viewport comments.

        All four are bounded: validations and conditional formats are capped to
        the first 200 rules for the sheet, and comments to the first 200 in the
        viewport range (enough for a markers overlay). The caller renders rules
        client-side against the same cell coordinates.
        """
        metadata: Dict[str, Any] = {
            "freeze": {"freeze_rows": 0, "freeze_columns": 0},
            "validations": [],
            "conditional_formats": [],
            "comments": [],
        }
        try:
            freeze = connection.execute(
                "SELECT freeze_rows, freeze_columns FROM sum_freeze WHERE sheet_id = ?",
                (sheet_id,),
            ).fetchone()
            if freeze:
                metadata["freeze"] = {
                    "freeze_rows": int(freeze["freeze_rows"]),
                    "freeze_columns": int(freeze["freeze_columns"]),
                }
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum viewport could not read freeze config for sheet {sheet_id}: {exc}"
            )
        try:
            validations = connection.execute(
                "SELECT id, row_start, row_end, column_start, column_end, rule_type, "
                "criteria_json, allow_blank, message FROM sum_validations "
                "WHERE sheet_id = ? LIMIT 200",
                (sheet_id,),
            ).fetchall()
            metadata["validations"] = [
                {
                    "id": str(v["id"]),
                    "row_start": int(v["row_start"]),
                    "row_end": int(v["row_end"]),
                    "column_start": int(v["column_start"]),
                    "column_end": int(v["column_end"]),
                    "rule_type": v["rule_type"],
                    "criteria": self._decode(v["criteria_json"], {}),
                    "allow_blank": bool(v["allow_blank"]),
                    "message": v["message"],
                }
                for v in validations
            ]
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum viewport could not read validations for sheet {sheet_id}: {exc}"
            )
        try:
            formats = connection.execute(
                "SELECT id, row_start, row_end, column_start, column_end, rule_type, "
                "criteria_json, style_json FROM sum_conditional_formats "
                "WHERE sheet_id = ? LIMIT 200",
                (sheet_id,),
            ).fetchall()
            metadata["conditional_formats"] = [
                {
                    "id": str(f["id"]),
                    "row_start": int(f["row_start"]),
                    "row_end": int(f["row_end"]),
                    "column_start": int(f["column_start"]),
                    "column_end": int(f["column_end"]),
                    "rule_type": f["rule_type"],
                    "criteria": self._decode(f["criteria_json"], {}),
                    "style": self._decode(f["style_json"], {}),
                }
                for f in formats
            ]
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum viewport could not read conditional formats for sheet {sheet_id}: {exc}"
            )
        try:
            comments = connection.execute(
                "SELECT id, row_index, column_index, author_id, body, resolved, "
                "created_at FROM sum_comments WHERE sheet_id = ? AND row_index >= ? "
                "AND row_index < ? AND column_index >= ? AND column_index < ? "
                "ORDER BY created_at LIMIT ?",
                (sheet_id, row_start, row_end, column_start, column_end, max_comments),
            ).fetchall()
            metadata["comments"] = [
                {
                    "id": str(c["id"]),
                    "row": int(c["row_index"]),
                    "column": int(c["column_index"]),
                    "author_id": int(c["author_id"]),
                    "body": c["body"],
                    "resolved": bool(c["resolved"]),
                    "created_at": int(c["created_at"]),
                }
                for c in comments
            ]
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum viewport could not read comments for sheet {sheet_id}: {exc}"
            )
        return metadata

    def read_viewport(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        filters: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        workbook = self._workbook(user_id, workbook_id)
        row_start, row_end = int(row_start), int(row_end)
        column_start, column_end = int(column_start), int(column_end)
        if (
            row_start < 0
            or column_start < 0
            or row_end <= row_start
            or column_end <= column_start
        ):
            raise SumValidationError("Invalid viewport bounds")
        if (row_end - row_start) * (
            column_end - column_start
        ) > self.MAX_VIEWPORT_CELLS:
            raise SumValidationError("Viewport is too large")
        normalized_filters: List[Dict[str, Any]] = []
        if filters:
            for criterion in filters[:50]:
                try:
                    column_index = int(criterion["column"])
                except (KeyError, TypeError, ValueError):
                    continue
                if column_index < 0 or column_index > 16_384:
                    continue
                operator = str(criterion.get("operator", "eq")).strip()
                if operator not in (
                    "eq",
                    "neq",
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "contains",
                    "not_contains",
                    "is_empty",
                    "is_not_empty",
                ):
                    continue
                normalized_filters.append(
                    {
                        "column": column_index,
                        "operator": operator,
                        "value": criterion.get("value"),
                    }
                )
        # Solo fast-path: read directly from the stored blob without a
        # persistent session. Only valid when there is NO live session AND the
        # stored blob is fully current — an existing session, or unflushed
        # operations in the durable log (blob_revision behind
        # current_revision, e.g. after a session was dropped), means the blob
        # would serve stale data and the read must go through the session,
        # which replays the log on materialization.
        if (
            self._is_solo(int(workbook_id))
            and int(workbook_id) not in _WORKBOOK_SESSIONS
        ):
            _rev_row = self._db.fetch_one(
                "SELECT blob_revision, current_revision FROM sum_workbooks "
                "WHERE id = ?",
                (int(workbook_id),),
            )
            if _rev_row is not None and int(_rev_row["blob_revision"]) >= int(
                _rev_row["current_revision"]
            ):
                return self._read_viewport_solo(
                    user_id,
                    workbook_id,
                    sheet_id,
                    row_start,
                    row_end,
                    column_start,
                    column_end,
                    normalized_filters,
                    workbook=workbook,
                )

        # Query the live session directly: hot viewport polls must not pay a
        # serialize + deserialize of the whole workbook per request. The
        # per-workbook lock is held for the duration of the read so the
        # connection cannot be dropped or mutated mid-query.
        _session_lock = self._session_lock(int(workbook_id))
        _session_lock.acquire()
        try:
            connection = self._materialized_connection_unlocked(int(workbook_id))
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            rows = connection.execute(
                "SELECT row_index, column_index, value_type, value_json, formula, style_json, revision FROM sum_cells WHERE sheet_id = ? AND row_index >= ? AND row_index < ? AND column_index >= ? AND column_index < ? ORDER BY row_index, column_index",
                (str(sheet_id), row_start, row_end, column_start, column_end),
            ).fetchall()
            # Check for segment-backed columns and merge with sparse cells
            segment_map: Dict[int, Dict[int, Dict[str, Any]]] = {}
            for col_idx in range(column_start, column_end):
                try:
                    seg_rows = connection.execute(
                        "SELECT segment_id, compressed_values FROM sum_column_segments "
                        "WHERE sheet_id = ? AND column_id = ? AND row_end > ? AND row_start < ?",
                        (str(sheet_id), str(col_idx), row_start, row_end),
                    ).fetchall()
                    if seg_rows:
                        col_cells = {}
                        for seg_row in seg_rows:
                            decoded = SegmentEncoder.decode_segment(
                                seg_row["compressed_values"]
                            )
                            for cell in decoded.get("cells", []) or []:
                                if not isinstance(cell, dict):
                                    continue
                                r = int(cell.get("row_index", cell.get("row", 0)))
                                if row_start <= r < row_end:
                                    col_cells[r] = cell
                        segment_map[col_idx] = col_cells
                except Exception:
                    pass
            # Merge: segment cells fill gaps, sparse cells take precedence
            if segment_map:
                existing_cells = {}
                for row in rows:
                    r, c = int(row["row_index"]), int(row["column_index"])
                    existing_cells[(r, c)] = row
                for col_idx, col_cells in segment_map.items():
                    for r, cell in col_cells.items():
                        if (r, col_idx) not in existing_cells:
                            rows_list = list(rows)
                            rows_list.append(
                                type(
                                    "Row",
                                    (),
                                    {
                                        "row_index": r,
                                        "column_index": col_idx,
                                        "value_type": cell.get("value_type", "blank"),
                                        "value_json": cell.get("value_json"),
                                        "formula": cell.get("formula"),
                                        "style_json": cell.get("style_json"),
                                        "revision": 0,
                                    },
                                )()
                            )
                            rows = rows_list
            used = connection.execute(
                "SELECT MAX(row_index) AS max_row, MAX(column_index) AS max_col FROM sum_cells WHERE sheet_id = ?",
                (str(sheet_id),),
            ).fetchone()

            cells: List[Dict[str, Any]] = []
            formula_cells: List[Tuple[int, int, Dict[str, Any]]] = []
            for row in rows:
                cell_data: Dict[str, Any] = {
                    "row": int(row["row_index"]),
                    "column": int(row["column_index"]),
                    "value_type": row["value_type"],
                    "value": self._decode(row["value_json"]),
                    "formula": row["formula"],
                    "style": self._decode(row["style_json"]),
                    "revision": int(row["revision"]),
                }
                if cell_data["value_type"] == "formula" and cell_data["formula"]:
                    formula_cells.append(
                        (cell_data["row"], cell_data["column"], cell_data)
                    )
                cells.append(cell_data)

            if formula_cells:
                resolver = self._resolver_for(connection)
                evaluator = FormulaEvaluator(resolver)
                for row_index, column_index, formula_cell in formula_cells:
                    formula_cell["computed"] = evaluator.evaluate_cell(
                        str(sheet_id), row_index, column_index, formula_cell["formula"]
                    )
            max_row = (
                int(used["max_row"]) + 1 if used and used["max_row"] is not None else 0
            )
            max_col = (
                int(used["max_col"]) + 1 if used and used["max_col"] is not None else 0
            )

            # Server-side filtering
            hidden_rows: List[int] = []
            if normalized_filters:
                by_row: Dict[int, List[Dict[str, Any]]] = {}
                for cell in cells:
                    by_row.setdefault(int(cell["row"]), []).append(cell)
                visible: Dict[int, bool] = {}
                for row_index in sorted(by_row):
                    row_cells = by_row[row_index]
                    matches = True
                    for criterion in normalized_filters:
                        column_index = int(criterion["column"])
                        cell: Optional[Dict[str, Any]] = next(
                            (c for c in row_cells if int(c["column"]) == column_index),
                            None,
                        )
                        value = self._filter_cell_value(cell)
                        if not self._matches_filter(
                            value, criterion["operator"], criterion.get("value")
                        ):
                            matches = False
                            break
                    visible[row_index] = matches
                hidden_rows = [
                    row_index for row_index in sorted(by_row) if not visible[row_index]
                ]

            metadata = self._read_viewport_metadata(
                connection,
                str(sheet_id),
                row_start,
                row_end,
                column_start,
                column_end,
            )
            try:
                session = _WORKBOOK_SESSIONS.get(int(workbook_id))
                if session is None or session.pending > 0:
                    self._refresh_workbook_status(connection, int(workbook_id))
            except Exception:
                pass
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file schema is invalid") from exc
        finally:
            _session_lock.release()
        viewer = self._viewer_permissions(
            user_id, int(workbook_id), sheet_id=int(sheet_id), workbook=workbook
        )
        return {
            "workbook_id": str(workbook_id),
            "sheet_id": str(sheet_id),
            "revision": int(workbook["current_revision"]),
            "row_start": row_start,
            "row_end": row_end,
            "column_start": column_start,
            "column_end": column_end,
            "row_count": max_row,
            "column_count": max_col,
            "has_more": bool(row_end < max_row or column_end < max_col),
            "cells": cells,
            "hidden_rows": hidden_rows,
            "filters": normalized_filters,
            "freeze": metadata["freeze"],
            "validations": metadata["validations"],
            "conditional_formats": metadata["conditional_formats"],
            "comments": metadata["comments"],
            "can_edit": bool(viewer["can_edit"]),
            "read_only_ranges": viewer["read_only_ranges"],
        }

    # ── operations ───────────────────────────────────────────────────────────

    def _check_range_permissions(
        self,
        workbook_id: int,
        sheet_id: int,
        cells: List[Dict[str, Any]],
        user_id: int,
    ) -> None:
        workbook = self._db.fetch_one(
            "SELECT owner_id FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
        )
        if workbook and int(workbook["owner_id"]) == int(user_id):
            return
        read_only = self._db.fetch_all(
            "SELECT row_start, row_end, column_start, column_end FROM sum_range_permissions WHERE workbook_id = ? AND sheet_id = ? AND permission = 'read_only'",
            (int(workbook_id), int(sheet_id)),
        )
        if not read_only:
            return
        for cell in cells:
            row = int(cell["row"])
            column = int(cell["column"])
            for rng in read_only:
                if int(rng["row_start"]) <= row < int(rng["row_end"]) and int(
                    rng["column_start"]
                ) <= column < int(rng["column_end"]):
                    raise SumPermissionError(
                        "This range is read-only for your access level"
                    )

    def apply_operation(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        operation_id: str,
        operation_type: str,
        base_revision: int,
        cells: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        import utils.logger as logger

        if not operation_id or len(str(operation_id)) > 200:
            raise SumValidationError("operation_id is required")
        if operation_type not in SUM_OPERATION_TYPES:
            raise SumValidationError("Unsupported operation type")
        if operation_type == "rename_sheet":
            raise SumValidationError(
                "rename_sheet is handled by the sheet rename endpoint"
            )
        if (
            not isinstance(cells, list)
            or not cells
            or len(cells) > self.MAX_OPERATION_CELLS
        ):
            raise SumValidationError("Operation must contain 1 to 2000 cells")

        workbook = self._workbook(user_id, workbook_id, mutation=True)
        current_revision = int(workbook["current_revision"])

        # Idempotency check against the main DB log (cheap, no session needed).
        existing_row = self._db.fetch_one(
            "SELECT result_revision FROM sum_operations "
            "WHERE workbook_id = ? AND operation_id = ?",
            (int(workbook_id), str(operation_id)),
        )
        if existing_row is not None:
            return {
                "operation_id": operation_id,
                "revision": int(existing_row["result_revision"]),
                "replayed": True,
            }

        if int(base_revision) != current_revision:
            raise SumConflictError(f"Workbook revision is {current_revision}")

        # Every operation flows through the coalesced live-session path. The
        # removed "solo fast-path" rewrote the entire blob on every operation
        # (one full upload per edit), skipped the durable main-database
        # operation log (breaking idempotent retries, audit, and crash
        # recovery), and bypassed the session/eviction machinery that the
        # revision cadence and multi-writer CAS safety depend on.
        with self._session_lock(int(workbook_id)):
            connection = self._materialized_connection_unlocked(int(workbook_id))
            existing = connection.execute(
                "SELECT result_revision FROM sum_operations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
            if existing is not None:
                return {
                    "operation_id": operation_id,
                    "revision": int(existing["result_revision"]),
                    "replayed": True,
                }

            if operation_type in STRUCTURAL_OPERATION_TYPES:
                data = self._serialize_connection(connection)
                return self._apply_structural_operation(
                    user_id,
                    workbook_id,
                    sheet_id,
                    operation_id,
                    operation_type,
                    current_revision,
                    cells,
                    data,
                )

        normalized = self._normalize_cells(operation_type, cells)

        revision = current_revision + 1
        now = int(time.time() * 1000)

        with self._session_lock(int(workbook_id)):
            connection = self._materialized_connection_unlocked(int(workbook_id))
            try:
                sheet = connection.execute(
                    "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                    (str(sheet_id),),
                ).fetchone()
                if sheet is None:
                    raise SumNotFoundError("Sheet not found")
                self._validate_cells(connection, str(sheet_id), normalized)
            except sqlite3.Error as exc:
                raise SumStorageError("Workbook file could not be updated") from exc

            self._check_range_permissions(
                int(workbook_id), int(sheet_id), normalized, user_id
            )

            self._append_op_durable(
                int(workbook_id),
                operation_id,
                user_id,
                current_revision,
                revision,
                operation_type,
                sheet_id,
                normalized,
                now,
                f"Workbook revision is {current_revision}",
            )
            try:
                for cell in normalized:
                    self._apply_normalized_cell(
                        connection, str(sheet_id), cell, revision, now
                    )
                connection.execute(
                    "INSERT INTO sum_operations (operation_id, actor_id, "
                    "base_revision, result_revision, operation_type, "
                    "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(operation_id),
                        user_id,
                        current_revision,
                        revision,
                        operation_type,
                        self._json({"sheet_id": str(sheet_id), "cells": normalized}),
                        now,
                    ),
                )
                self._ensure_axis_metadata(connection, str(sheet_id), normalized)
                self._bump_statistics(connection, str(sheet_id), normalized)
                connection.commit()
            except sqlite3.Error as exc:
                self._drop_session(int(workbook_id))
                raise SumStorageError("Workbook file could not be updated") from exc
            session = _WORKBOOK_SESSIONS.get(int(workbook_id))
            if session is not None:
                session.pending += 1
            self._maybe_flush(int(workbook_id))
            # Keep the cached index/calculation status current after every
            # mutation: two COUNTs on the in-memory session plus one main-DB
            # UPDATE. The materialization-time refresh only fires when a
            # session is rebuilt (blob behind the log), which misses the very
            # first buffered operation of a fresh workbook.
            session = _WORKBOOK_SESSIONS.get(int(workbook_id))
            if session is not None:
                try:
                    self._refresh_workbook_status(session.connection, int(workbook_id))
                except Exception:
                    logger.debug(
                        "PlexiSum workbook status refresh failed", exc_info=True
                    )

        self._audit(
            "edit",
            "operation",
            workbook_id,
            user_id,
            {
                "operation_id": str(operation_id),
                "sheet_id": str(sheet_id),
                "revision": revision,
                "operation_type": operation_type,
                "cell_count": len(normalized),
            },
        )
        self._emit_update(workbook, revision, user_id)
        return {
            "operation_id": operation_id,
            "revision": revision,
            "replayed": False,
        }

    def _normalize_cells(
        self, operation_type: str, cells: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Normalize raw operation cells into the canonical internal format."""
        normalized: List[Dict[str, Any]] = []
        for cell in cells:
            try:
                row_index = int(cell["row"])
                column_index = int(cell["column"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SumValidationError(
                    "Each cell needs numeric row and column"
                ) from exc
            if (
                row_index < 0
                or column_index < 0
                or row_index > 10_000_000
                or column_index > 16_384
            ):
                raise SumValidationError("Cell coordinates are out of bounds")
            if operation_type == "clear_cell":
                normalized.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "value_type": "blank",
                        "value": None,
                        "formula": None,
                        "style": cell.get("style"),
                    }
                )
                continue
            if operation_type == "set_format":
                normalized.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "value_type": "__preserve__",
                        "value": "__preserve__",
                        "formula": "__preserve__",
                        "style": cell.get("style"),
                    }
                )
                continue
            value_type = str(cell.get("value_type", "blank"))
            if value_type not in SUM_VALUE_TYPES:
                raise SumValidationError("Unknown cell value type")
            normalized.append(
                {
                    "row": row_index,
                    "column": column_index,
                    "value_type": value_type,
                    "value": cell.get("value"),
                    "formula": cell.get("formula"),
                    "style": cell.get("style"),
                }
            )
        return normalized

    # ── structural operations (rows / columns) ───────────────────────────────

    def _apply_structural_operation(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        operation_id: str,
        operation_type: str,
        base_revision: int,
        cells: List[Dict[str, Any]],
        data: bytes,
    ) -> Dict[str, Any]:
        """Insert or delete rows/columns as a single revision-bumping operation.

        The payload is an anchor cell plus a count, e.g.
        ``[{"row": 3, "column": 0, "count": 2}]`` inserts two rows at row 3,
        or ``[{"row": 0, "column": 2, "count": 1}]`` deletes one column at
        column 2. Cell coordinates, axis metadata, query statistics, managed
        indexes, and protected ranges are all shifted consistently.
        """
        if not isinstance(cells, list) or not cells:
            raise SumValidationError("Structural operation needs an anchor cell")
        try:
            anchor = cells[0]
            count = int(anchor.get("count", 1))
        except (KeyError, TypeError, ValueError) as exc:
            raise SumValidationError(
                "Structural operation needs a numeric count"
            ) from exc
        if operation_type.endswith("rows"):
            try:
                at = int(anchor["row"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SumValidationError(
                    "Structural operation needs a numeric row anchor"
                ) from exc
            axis = "row"
        else:
            try:
                at = int(anchor["column"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SumValidationError(
                    "Structural operation needs a numeric column anchor"
                ) from exc
            axis = "column"
        if at < 0:
            raise SumValidationError("Anchor index must be non-negative")
        if count < 1 or count > MAX_STRUCTURAL_CHANGE:
            raise SumValidationError(
                f"count must be between 1 and {MAX_STRUCTURAL_CHANGE}"
            )
        self._check_structural_permission(
            int(workbook_id),
            str(sheet_id),
            axis,
            at,
            count,
            operation_type.startswith("insert"),
            user_id,
        )

        now = int(time.time() * 1000)
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            if operation_type == "insert_rows":
                self._shift_axis(connection, str(sheet_id), "row", at, count, True)
            elif operation_type == "delete_rows":
                self._shift_axis(connection, str(sheet_id), "row", at, count, False)
            elif operation_type == "insert_columns":
                self._shift_axis(connection, str(sheet_id), "column", at, count, True)
            else:
                self._shift_axis(connection, str(sheet_id), "column", at, count, False)
            connection.execute(
                "INSERT INTO sum_operations (operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(operation_id),
                    user_id,
                    base_revision,
                    base_revision + 1,
                    operation_type,
                    self._json(
                        {
                            "sheet_id": str(sheet_id),
                            "anchor": {"at": at, "count": count, "axis": axis},
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)

        revision = self._write_file_revision(
            workbook_id, user_id, operation_type, updated
        )
        self._db.execute(
            "INSERT INTO sum_operations (operation_id, workbook_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(operation_id),
                int(workbook_id),
                user_id,
                base_revision,
                revision,
                operation_type,
                self._json(
                    {
                        "sheet_id": str(sheet_id),
                        "anchor": {"at": at, "count": count, "axis": axis},
                    }
                ),
                now,
            ),
        )
        self._shift_range_permissions(
            int(workbook_id),
            str(sheet_id),
            axis,
            at,
            count,
            operation_type.startswith("insert"),
        )
        return {
            "operation_id": operation_id,
            "revision": revision,
            "replayed": False,
        }

    def _shift_axis(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        axis: str,
        at: int,
        count: int,
        insert: bool,
    ) -> None:
        """Shift cells, axis metadata, statistics, and indexes for an insert/delete.

        Uses SQL range UPDATEs for efficient bulk shifts instead of
        read-all/delete/reinsert loops.
        """
        if axis == "row":
            index_field = "row_index"
            axis_table = "sum_rows"
            column_key_field = None
        else:
            index_field = "column_index"
            axis_table = "sum_columns"
            column_key_field = "column_key"

        # 1. Keep the MAX_STRUCTURAL_CELLS check
        source_bound = at if insert else at + count
        count_check = connection.execute(
            f"SELECT COUNT(*) as c FROM sum_cells WHERE sheet_id = ? AND {index_field} >= ?",
            (sheet_id, source_bound),
        ).fetchone()
        if count_check["c"] > MAX_STRUCTURAL_CELLS:
            raise SumValidationError(
                "Operation affects too many cells; split it into smaller changes"
            )

        # 2. Remove the deleted band first (delete ops only).
        if not insert:
            connection.execute(
                f"DELETE FROM sum_cells WHERE sheet_id = ? AND {index_field} >= ? AND {index_field} < ?",
                (sheet_id, at, at + count),
            )

        # 3. Move the surviving cells using SQL range UPDATE.
        if insert:
            connection.execute(
                f"UPDATE sum_cells SET {index_field} = {index_field} + ? "
                f"WHERE sheet_id = ? AND {index_field} >= ?",
                (count, sheet_id, at),
            )
        else:
            connection.execute(
                f"UPDATE sum_cells SET {index_field} = {index_field} - ? "
                f"WHERE sheet_id = ? AND {index_field} >= ?",
                (count, sheet_id, at + count),
            )

        # 4. Axis metadata (sum_rows / sum_columns) stays in sync.
        if insert:
            connection.execute(
                f"UPDATE {axis_table} SET position = position + ? WHERE sheet_id = ? AND position >= ?",
                (count, sheet_id, at),
            )
            now = int(time.time() * 1000)
            rows_data = [
                (str(self._generate_id()), sheet_id, f"{axis}-{i}", i, now)
                for i in range(at, at + count)
            ]
            if axis == "row":
                connection.executemany(
                    "INSERT OR IGNORE INTO sum_rows (id, sheet_id, stable_key, position, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows_data,
                )
            else:
                cols_data = [
                    (
                        str(self._generate_id()),
                        sheet_id,
                        f"{axis}-{i}",
                        _column_letter(i),
                        i,
                        now,
                    )
                    for i in range(at, at + count)
                ]
                connection.executemany(
                    "INSERT OR IGNORE INTO sum_columns (id, sheet_id, stable_key, display_name, data_type, position, created_at) "
                    "VALUES (?, ?, ?, ?, 'text', ?, ?)",
                    cols_data,
                )
        else:
            connection.execute(
                f"DELETE FROM {axis_table} WHERE sheet_id = ? AND position >= ? AND position < ?",
                (sheet_id, at, at + count),
            )
            connection.execute(
                f"UPDATE {axis_table} SET position = position - ? WHERE sheet_id = ? AND position >= ?",
                (count, sheet_id, at + count),
            )

        # 5. Query statistics and managed indexes follow column moves.
        if column_key_field:
            if insert:
                connection.execute(
                    "UPDATE sum_statistics SET column_key = CAST(CAST(column_key AS INTEGER) + ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
                    (count, sheet_id, at),
                )
                connection.execute(
                    "UPDATE sum_indexes SET column_key = CAST(CAST(column_key AS INTEGER) + ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
                    (count, sheet_id, at),
                )
            else:
                connection.execute(
                    "DELETE FROM sum_statistics WHERE sheet_id = ? "
                    "AND CAST(column_key AS INTEGER) >= ? AND CAST(column_key AS INTEGER) < ?",
                    (sheet_id, at, at + count),
                )
                connection.execute(
                    "UPDATE sum_statistics SET column_key = CAST(CAST(column_key AS INTEGER) - ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
                    (count, sheet_id, at + count),
                )
                connection.execute(
                    "DELETE FROM sum_indexes WHERE sheet_id = ? "
                    "AND CAST(column_key AS INTEGER) >= ? AND CAST(column_key AS INTEGER) < ?",
                    (sheet_id, at, at + count),
                )
                connection.execute(
                    "UPDATE sum_indexes SET column_key = CAST(CAST(column_key AS INTEGER) - ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
                    (count, sheet_id, at + count),
                )

        # 6. Column segments follow column moves.
        if column_key_field:
            if insert:
                connection.execute(
                    "UPDATE sum_column_segments SET column_id = CAST(CAST(column_id AS INTEGER) + ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_id AS INTEGER) >= ?",
                    (count, sheet_id, at),
                )
            else:
                connection.execute(
                    "DELETE FROM sum_column_segments WHERE sheet_id = ? "
                    "AND CAST(column_id AS INTEGER) >= ? AND CAST(column_id AS INTEGER) < ?",
                    (sheet_id, at, at + count),
                )
                connection.execute(
                    "UPDATE sum_column_segments SET column_id = CAST(CAST(column_id AS INTEGER) - ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_id AS INTEGER) >= ?",
                    (count, sheet_id, at + count),
                )

        # Index entries embed row/column coordinates; invalidate and re-promote.
        self._invalidate_sheet_indexes(connection, sheet_id)

    def _check_structural_permission(
        self,
        workbook_id: int,
        sheet_id: str,
        axis: str,
        at: int,
        count: int,
        insert: bool,
        user_id: int,
    ) -> None:
        """Block deleting cells inside read-only protected ranges."""
        workbook = self._db.fetch_one(
            "SELECT owner_id FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
        )
        if workbook and int(workbook["owner_id"]) == int(user_id):
            return
        if insert:
            return
        read_only = self._db.fetch_all(
            "SELECT row_start, row_end, column_start, column_end FROM sum_range_permissions "
            "WHERE workbook_id = ? AND sheet_id = ? AND permission = 'read_only'",
            (int(workbook_id), int(sheet_id)),
        )
        if not read_only:
            return
        band_start, band_end = at, at + count
        for rng in read_only:
            if axis == "row":
                overlaps = (
                    int(rng["row_start"]) < band_end
                    and int(rng["row_end"]) > band_start
                )
            else:
                overlaps = (
                    int(rng["column_start"]) < band_end
                    and int(rng["column_end"]) > band_start
                )
            if overlaps:
                raise SumPermissionError("Cannot delete cells inside a protected range")

    def _invalidate_sheet_indexes(
        self, connection: sqlite3.Connection, sheet_id: str
    ) -> None:
        """Drop index entries for a sheet and mark indexes pending re-promotion."""
        try:
            connection.execute(
                "DELETE FROM sum_index_entries WHERE index_id IN "
                "(SELECT id FROM sum_indexes WHERE sheet_id = ?)",
                (sheet_id,),
            )
            connection.execute(
                "UPDATE sum_indexes SET state = 'pending' WHERE sheet_id = ?",
                (sheet_id,),
            )
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum could not invalidate sheet indexes for sheet {sheet_id}: {exc}"
            )
            raise

    def _shift_range_permissions(
        self,
        workbook_id: int,
        sheet_id: str,
        axis: str,
        at: int,
        count: int,
        insert: bool,
    ) -> None:
        """Keep protected ranges aligned with the cells they protect.

        Ranges that fully precede the change stay put; ranges fully after the
        change shift by the delta; ranges that straddle the changed band are
        removed (the safe default for a layout change). Best-effort and
        idempotent; failures only affect range metadata, never cell data.
        """
        try:
            if axis == "row":
                start_field, end_field = "row_start", "row_end"
            else:
                start_field, end_field = "column_start", "column_end"
            if insert:
                self._db.execute(
                    f"UPDATE sum_range_permissions SET {start_field} = {start_field} + ?, {end_field} = {end_field} + ? WHERE workbook_id = ? AND sheet_id = ? AND {start_field} >= ?",
                    (count, count, workbook_id, int(sheet_id), at),
                )
            else:
                band_start, band_end = at, at + count
                # Drop ranges that overlap the deleted band.
                self._db.execute(
                    f"DELETE FROM sum_range_permissions WHERE workbook_id = ? AND sheet_id = ? AND {start_field} < ? AND {end_field} > ?",
                    (workbook_id, int(sheet_id), band_end, band_start),
                )
                # Shift ranges that lie entirely after the band.
                self._db.execute(
                    f"UPDATE sum_range_permissions SET {start_field} = {start_field} - ?, {end_field} = {end_field} - ? WHERE workbook_id = ? AND sheet_id = ? AND {start_field} >= ?",
                    (count, count, workbook_id, int(sheet_id), band_end),
                )
        except Exception:
            import utils.logger as logger

            logger.warning("PlexiSum range-permission shift failed", exc_info=True)

    def _ensure_axis_metadata(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        cells: List[Dict[str, Any]],
    ) -> None:
        """Keep the logical ``sum_rows``/``sum_columns`` axis tables in sync.

        The dynamic schema hooks are only useful if they reflect written data;
        this makes the axis metadata real without ever interpolating user
        values into DDL (stable keys are generated, not user-controlled).
        """
        now = int(time.time() * 1000)
        rows_needed: set = set()
        columns_needed: set = set()
        for cell in cells:
            rows_needed.add(int(cell["row"]))
            columns_needed.add(int(cell["column"]))

        if rows_needed:
            rows_data = [
                (str(self._generate_id()), sheet_id, f"row-{r}", r, now)
                for r in rows_needed
            ]
            connection.executemany(
                "INSERT OR IGNORE INTO sum_rows (id, sheet_id, stable_key, position, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows_data,
            )

        if columns_needed:
            cols_data = [
                (
                    str(self._generate_id()),
                    sheet_id,
                    f"col-{c}",
                    _column_letter(c),
                    c,
                    now,
                )
                for c in columns_needed
            ]
            connection.executemany(
                "INSERT OR IGNORE INTO sum_columns (id, sheet_id, stable_key, display_name, data_type, position, created_at) "
                "VALUES (?, ?, ?, ?, 'text', ?, ?)",
                cols_data,
            )

    def _bump_statistics(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        cells: List[Dict[str, Any]],
    ) -> None:
        """Record per-column access so auto-indexes can be promoted.

        Index promotion is only attempted for columns that cross the access
        threshold during this operation. Previously it ran a full scan of the
        hottest columns on every write, stalling the operation for O(column
        size) work even when nothing new qualified.
        """
        now = int(time.time() * 1000)
        counts: Dict[int, int] = {}
        for cell in cells:
            counts[int(cell["column"])] = counts.get(int(cell["column"]), 0) + 1

        column_keys = list(counts.keys())
        if not column_keys:
            return

        # Batch fetch existing counts
        placeholders = ",".join("?" * len(column_keys))
        existing = connection.execute(
            f"SELECT column_key, access_count FROM sum_statistics "
            f"WHERE sheet_id = ? AND column_key IN ({placeholders})",
            (sheet_id, *[str(k) for k in column_keys]),
        ).fetchall()
        existing_map = {str(r["column_key"]): int(r["access_count"]) for r in existing}

        # Batch upsert
        rows = []
        crossed: List[str] = []
        for column_index, cell_count in counts.items():
            column_key = str(column_index)
            previous = existing_map.get(column_key, 0)
            rows.append((sheet_id, column_key, cell_count, now))
            if previous < INDEX_PROMOTION_THRESHOLD <= previous + cell_count:
                crossed.append(column_key)

        connection.executemany(
            "INSERT INTO sum_statistics (sheet_id, column_key, access_count, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(sheet_id, column_key) DO UPDATE SET "
            "access_count = sum_statistics.access_count + excluded.access_count, "
            "updated_at = excluded.updated_at",
            rows,
        )

        if crossed:
            try:
                self._promote_indexes(connection, sheet_id, now, crossed)
            except sqlite3.Error as exc:
                import utils.logger as logger

                logger.error(
                    f"PlexiSum could not promote auto-indexes for sheet {sheet_id}: {exc}"
                )
                raise
        # Re-check pending indexes: a column that crossed the threshold while
        # all its cells were blank is marked 'pending' (its access count never
        # drops below the threshold again, so it cannot "re-cross"). This op
        # may have populated it, so promote it now.
        try:
            pending = [
                str(r["column_key"])
                for r in connection.execute(
                    "SELECT column_key FROM sum_indexes "
                    "WHERE sheet_id = ? AND state = 'pending'",
                    (sheet_id,),
                ).fetchall()
            ]
            if pending:
                self._promote_indexes(connection, sheet_id, now, pending)
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum could not promote pending auto-indexes for sheet {sheet_id}: {exc}"
            )
            raise

    def _promote_indexes(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        now: int,
        columns: List[str],
    ) -> None:
        """Materialize sorted indexes for columns that just crossed the threshold.

        Only the columns passed in are considered (they were measured as newly
        hot in this operation), so steady-state writes never scan columns.
        Index ids use a fresh snowflake to guarantee uniqueness even when two
        promotions happen within the same millisecond.
        """
        existing = {
            str(r["column_key"])
            for r in connection.execute(
                "SELECT column_key FROM sum_indexes WHERE sheet_id = ? AND state IN ('ready', 'building')",
                (sheet_id,),
            ).fetchall()
        }  # Pending markers are placeholders, not materialized indexes, so they
        # must not consume the per-sheet index budget.
        count = connection.execute(
            "SELECT COUNT(*) AS c FROM sum_indexes "
            "WHERE sheet_id = ? AND state != 'pending'",
            (sheet_id,),
        ).fetchone()["c"]
        for column_key in columns:
            if column_key in existing or int(count) >= MAX_INDEXES_PER_SHEET:
                continue
            index_id = f"idx-{sheet_id}-{column_key}-{self._generate_id()}"
            entries = connection.execute(
                "SELECT row_index, value_type, value_json, formula, style_json "
                "FROM sum_cells WHERE sheet_id = ? AND column_index = ? AND value_type != 'blank'",
                (sheet_id, int(column_key)),
            ).fetchall()
            if not entries:
                # No data yet: record a pending marker so a later op that
                # populates the column can promote it. Deleting the marker
                # would make the promotion unreachable because access_count
                # never drops below the threshold again.
                connection.execute(
                    "INSERT OR IGNORE INTO sum_indexes (id, sheet_id, column_key, index_type, state, created_at) VALUES (?, ?, ?, 'sorted', 'pending', ?)",
                    (f"idx-pending-{sheet_id}-{column_key}", sheet_id, column_key, now),
                )
                continue
            connection.execute(
                "INSERT INTO sum_indexes (id, sheet_id, column_key, index_type, state, created_at) VALUES (?, ?, ?, 'sorted', 'building', ?)",
                (index_id, sheet_id, column_key, now),
            )
            connection.executemany(
                "INSERT OR REPLACE INTO sum_index_entries (index_id, value_json, row_index) VALUES (?, ?, ?)",
                [(index_id, r["value_json"], int(r["row_index"])) for r in entries],
            )
            connection.execute(
                "UPDATE sum_indexes SET state = 'ready' WHERE id = ?", (index_id,)
            )
            # A promoted column no longer needs its pending marker.
            connection.execute(
                "DELETE FROM sum_indexes WHERE sheet_id = ? AND column_key = ? AND state = 'pending'",
                (sheet_id, column_key),
            )
            count += 1

            # Pack column into dense segments when it has enough cells
            if len(entries) >= 1000:
                try:
                    segments = SegmentEncoder.pack_column(entries)
                    for seg_id, seg_data in segments:
                        decoded = SegmentEncoder.decode_segment(seg_data)
                        connection.execute(
                            "INSERT OR REPLACE INTO sum_column_segments "
                            "(sheet_id, column_id, segment_id, row_start, row_end, "
                            "encoding, compressed_values, statistics_json) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                sheet_id,
                                column_key,
                                seg_id,
                                decoded["row_start"],
                                decoded["row_end"],
                                decoded["encoding"],
                                seg_data,
                                json.dumps(decoded.get("statistics", {})),
                            ),
                        )
                except Exception:
                    pass

    # ── replay / snapshots ────────────────────────────────────────────────────

    def replay_operations(
        self,
        user_id: int,
        workbook_id: int,
        after_revision: int = 0,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        self._workbook(user_id, workbook_id)
        with self._session_lock(int(workbook_id)):
            connection = self._materialized_connection_unlocked(int(workbook_id))
            try:
                rows = connection.execute(
                    "SELECT operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at FROM sum_operations WHERE result_revision > ? ORDER BY result_revision ASC LIMIT ?",
                    (int(after_revision), limit),
                ).fetchall()
            except sqlite3.Error as exc:
                raise SumStorageError("Workbook file schema is invalid") from exc
        operations = []
        for row in rows:
            payload = self._decode(row["payload_json"], {})
            operations.append(
                {
                    "operation_id": row["operation_id"],
                    "actor_id": str(row["actor_id"]),
                    "base_revision": int(row["base_revision"]),
                    "result_revision": int(row["result_revision"]),
                    "operation_type": row["operation_type"],
                    "sheet_id": payload.get("sheet_id"),
                    "cells": payload.get("cells", []),
                    "created_at": int(row["created_at"]),
                }
            )
        return operations

    def create_snapshot(self, user_id: int, workbook_id: int) -> Dict[str, Any]:
        workbook = self._workbook(user_id, workbook_id, mutation=True)
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum snapshot writes require a standalone transaction"
            )
        data = self._file_bytes(workbook_id)
        snapshot_id = self._generate_id()
        key = f"{int(workbook_id)}/snapshot-{snapshot_id}.plexisum"
        checksum = hashlib.sha256(data).hexdigest()
        now = int(time.time() * 1000)
        connection = None
        tmp_path = None
        try:
            connection, tmp_path = self._open_sqlite(data)
            cell_count = int(
                connection.execute("SELECT COUNT(*) AS c FROM sum_cells").fetchone()[
                    "c"
                ]
            )
        except (sqlite3.Error, SumStorageError) as exc:
            raise SumStorageError("Snapshot source workbook could not be read") from exc
        finally:
            if connection is not None and tmp_path is not None:
                self._close_sqlite(connection, tmp_path)

        # Validate the source before creating the external snapshot object. This
        # keeps schema/format failures from producing an unreachable blob.
        from .container import encode_container

        container_data = encode_container(data)
        path = self._blob.store(container_data, key)
        try:
            self._db.begin_transaction()
            self._db.execute(
                "INSERT INTO sum_snapshots (id, workbook_id, created_by, revision, blob_path, checksum, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    workbook_id,
                    user_id,
                    int(workbook["current_revision"]),
                    path,
                    checksum,
                    now,
                ),
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            if not self._delete_blob_confirmed(path):
                # Preserve a durable retry pointer when storage refuses the
                # compensating delete. A later GC/account purge can retry it.
                try:
                    self._db.begin_transaction()
                    self._db.insert_or_ignore(
                        "sum_snapshots",
                        [
                            "id",
                            "workbook_id",
                            "created_by",
                            "revision",
                            "blob_path",
                            "checksum",
                            "created_at",
                        ],
                        (
                            snapshot_id,
                            workbook_id,
                            user_id,
                            int(workbook["current_revision"]),
                            path,
                            checksum,
                            now,
                        ),
                    )
                    self._db.commit()
                except Exception:
                    if getattr(self._db, "in_transaction", False):
                        self._db.rollback()
            raise
        self._gc_snapshots(int(workbook_id))
        self._audit(
            "snapshot",
            "workbook",
            workbook_id,
            user_id,
            {
                "snapshot_id": str(snapshot_id),
                "revision": int(workbook["current_revision"]),
            },
        )
        return {
            "id": str(snapshot_id),
            "workbook_id": str(workbook_id),
            "revision": int(workbook["current_revision"]),
            "checksum": checksum,
            "cell_count": cell_count,
            "created_at": now,
        }

    def _gc_snapshots(self, workbook_id: int) -> None:
        """Retain only the newest ``MAX_SNAPSHOTS_KEPT`` snapshots per workbook.

        Snapshot blobs are immutable copies of the workbook file and would
        otherwise accumulate without bound. Best-effort and idempotent.
        """
        try:
            count_row = self._db.fetch_one(
                "SELECT COUNT(*) AS c FROM sum_snapshots WHERE workbook_id = ?",
                (int(workbook_id),),
            )
            count = int(count_row["c"]) if count_row else 0
            if count <= MAX_SNAPSHOTS_KEPT:
                return
            stale = self._db.fetch_all(
                "SELECT id, blob_path FROM sum_snapshots WHERE workbook_id = ? "
                "ORDER BY created_at ASC LIMIT ?",
                (int(workbook_id), count - MAX_SNAPSHOTS_KEPT),
            )
            for snap in stale:
                snap_path = str(snap.get("blob_path") or "")
                if snap_path and not self._delete_blob_confirmed(snap_path):
                    # Keep the row as a retry pointer. Removing it here would
                    # orphan a snapshot blob after a transient storage failure.
                    continue
                self._db.execute(
                    "DELETE FROM sum_snapshots WHERE id = ?", (int(snap["id"]),)
                )
        except Exception:
            import utils.logger as logger

            logger.warning("PlexiSum snapshot GC failed", exc_info=True)

    def list_snapshots(
        self, user_id: int, workbook_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        self._workbook(user_id, workbook_id)
        rows = self._db.fetch_all(
            "SELECT id, created_by, revision, checksum, created_at FROM sum_snapshots WHERE workbook_id = ? ORDER BY created_at DESC LIMIT ?",
            (int(workbook_id), limit),
        )
        return [
            {
                "id": str(row["id"]),
                "workbook_id": str(workbook_id),
                "created_by": str(row["created_by"]),
                "revision": int(row["revision"]),
                "checksum": row.get("checksum"),
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def restore_snapshot(
        self, user_id: int, workbook_id: int, snapshot_id: int
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        row = self._db.fetch_one(
            "SELECT revision, blob_path, checksum FROM sum_snapshots WHERE id = ? AND workbook_id = ?",
            (int(snapshot_id), int(workbook_id)),
        )
        if not row or not row.get("blob_path"):
            raise SumNotFoundError("Snapshot not found")
        try:
            container_data = self._blob.retrieve(str(row["blob_path"]))
        except Exception as exc:
            raise SumStorageError("Snapshot file could not be read") from exc
        if container_data[:4] == b"PXSM":
            from .container import decode_container

            db_bytes, _, _ = decode_container(container_data)
        else:
            db_bytes = container_data
        if row.get("checksum"):
            actual = hashlib.sha256(db_bytes).hexdigest()
            if actual != row["checksum"]:
                raise SumValidationError("Snapshot checksum mismatch")
        current = self._db.fetch_one(
            "SELECT current_revision FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        base_revision = int(current["current_revision"]) if current else 0
        revision = base_revision + 1
        now = int(time.time() * 1000)
        path, size, checksum = self._publish_revision(
            int(workbook_id),
            int(base_revision),
            int(revision),
            db_bytes,
            now,
            "Workbook revision changed during restore",
        )
        self._audit(
            "restore_snapshot",
            "workbook",
            workbook_id,
            user_id,
            {"snapshot_id": str(snapshot_id), "revision": revision},
        )
        try:
            row = self._db.fetch_one(
                "SELECT * FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
            )
            if row:
                self._emit_update(row, revision, user_id)
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum update emit failed", exc_info=True)
        return self.get_workbook(user_id, workbook_id)

    # ── sheet management ─────────────────────────────────────────────────────

    def create_sheet(
        self, user_id: int, workbook_id: int, name: str, position: Optional[int] = None
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        name = str(name or "").strip()
        if not name or len(name) > 200:
            raise SumValidationError("sheet name must be between 1 and 200 characters")
        data = self._file_bytes(workbook_id)
        now = int(time.time() * 1000)
        sheet_id = self._generate_id()
        connection, path = self._open_sqlite(data)
        try:
            duplicate = connection.execute(
                "SELECT id FROM sum_sheets WHERE name = ? AND deleted = 0",
                (name,),
            ).fetchone()
            if duplicate:
                raise SumValidationError("A sheet with this name already exists")
            if position is None:
                max_row = connection.execute(
                    "SELECT MAX(position) AS max_position FROM sum_sheets WHERE deleted = 0"
                ).fetchone()
                position = (
                    int(max_row["max_position"]) + 1
                    if max_row and max_row["max_position"] is not None
                    else 0
                )
            connection.execute(
                "INSERT INTO sum_sheets (id, name, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (str(sheet_id), name, int(position), now, now),
            )
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)
        self._write_file_revision(workbook_id, user_id, "create_sheet", updated)
        return {
            "id": str(sheet_id),
            "name": name,
            "position": int(position),
            "created_at": now,
            "updated_at": now,
        }

    def rename_sheet(
        self, user_id: int, workbook_id: int, sheet_id: int, name: str
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        name = str(name or "").strip()
        if not name or len(name) > 200:
            raise SumValidationError("sheet name must be between 1 and 200 characters")
        data = self._file_bytes(workbook_id)
        now = int(time.time() * 1000)
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            duplicate = connection.execute(
                "SELECT id FROM sum_sheets WHERE name = ? AND id != ? AND deleted = 0",
                (name, str(sheet_id)),
            ).fetchone()
            if duplicate:
                raise SumValidationError("A sheet with this name already exists")
            connection.execute(
                "UPDATE sum_sheets SET name = ?, updated_at = ? WHERE id = ?",
                (name, now, str(sheet_id)),
            )
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)
        self._write_file_revision(workbook_id, user_id, "rename_sheet", updated)
        return {"id": str(sheet_id), "name": name}

    def delete_sheet(self, user_id: int, workbook_id: int, sheet_id: int) -> None:
        self._workbook(user_id, workbook_id, mutation=True)
        data = self._file_bytes(workbook_id)
        now = int(time.time() * 1000)
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            remaining = connection.execute(
                "SELECT COUNT(*) AS count FROM sum_sheets WHERE deleted = 0 AND id != ?",
                (str(sheet_id),),
            ).fetchone()
            if not remaining or int(remaining["count"]) == 0:
                raise SumValidationError("A workbook must keep at least one sheet")
            connection.execute(
                "UPDATE sum_sheets SET deleted = 1, updated_at = ? WHERE id = ?",
                (now, str(sheet_id)),
            )
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)
        self._write_file_revision(workbook_id, user_id, "delete_sheet", updated)

    def _write_file_revision(
        self, workbook_id: int, user_id: int, action: str, updated: bytes
    ) -> int:
        current = self._db.fetch_one(
            "SELECT current_revision FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        base = int(current["current_revision"]) if current else 0
        revision = base + 1
        now = int(time.time() * 1000)
        path, size, checksum = self._publish_revision(
            int(workbook_id),
            int(base),
            int(revision),
            updated,
            now,
            "Workbook revision changed during update",
        )
        self._audit(
            action,
            "workbook",
            workbook_id,
            user_id,
            {"revision": revision},
        )
        try:
            row = self._db.fetch_one(
                "SELECT * FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
            )
            if row:
                self._emit_update(row, revision, user_id)
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum update emit failed", exc_info=True)
        return revision

    # ── sorting ──────────────────────────────────────────────────────────────

    def sort_range(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        column: int,
        start_row: int,
        end_row: int,
        direction: str,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Stable-sort a row range by a column's computed value.

        Blank key cells sort last. Formula keys are evaluated live. The whole
        range moves in one file rewrite (bounded), matching the operation
        model used by every other mutation.
        """
        workbook = self._workbook(user_id, workbook_id, mutation=True)
        current_revision = int(workbook["current_revision"])
        if int(base_revision) != current_revision:
            raise SumConflictError(f"Workbook revision is {current_revision}")
        if end_row <= start_row:
            raise SumValidationError("end_row must be greater than start_row")
        if end_row - start_row > MAX_CSV_ROWS:
            raise SumValidationError("Sort range is too large")
        if column < 0 or column > 16_384:
            raise SumValidationError("Column is out of bounds")
        direction = str(direction or "asc").lower()
        if direction not in ("asc", "desc"):
            raise SumValidationError("direction must be asc or desc")

        data = self._file_bytes(workbook_id)
        now = int(time.time() * 1000)
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            rows = connection.execute(
                "SELECT row_index, column_index, value_type, value_json, formula, style_json, revision, updated_at FROM sum_cells WHERE sheet_id = ? AND row_index >= ? AND row_index < ? ORDER BY row_index, column_index",
                (str(sheet_id), start_row, end_row),
            ).fetchall()
            if len(rows) > MAX_STRUCTURAL_CELLS:
                raise SumValidationError("Sort range affects too many cells")
            by_row: Dict[int, List[Any]] = {}
            for row in rows:
                by_row.setdefault(int(row["row_index"]), []).append(row)
            row_keys = sorted(by_row)
            if len(row_keys) < 2:
                return {
                    "operation_id": f"sort-{time.time_ns()}",
                    "revision": current_revision,
                    "rows": 0,
                }

            resolver = self._resolver_for(connection)
            evaluator = FormulaEvaluator(resolver)

            def sort_key(row_index: int) -> Tuple[int, float, str, int]:
                cell = resolver(str(sheet_id), row_index, column)
                if cell is None:
                    return (2, 0.0, "", row_index)
                formula = cell.get("formula")
                if formula:
                    value = evaluator.evaluate_cell(
                        str(sheet_id), row_index, column, formula
                    )
                    if is_error(value):
                        value = None
                else:
                    value = cell.get("value")
                if isinstance(value, bool):
                    return (0, 1.0 if value else 0.0, "", row_index)
                if isinstance(value, (int, float)):
                    return (0, float(value), "", row_index)
                if value is None or value == "":
                    return (2, 0.0, "", row_index)
                return (1, 0.0, str(value).lower(), row_index)

            def directed_key(row_index: int) -> Tuple[int, float, str, int]:
                # Always sort ascending; only the in-group value component is
                # direction-flipped. Reversing the whole tuple instead would
                # bring the blank group (2) to the top on a descending sort,
                # where Excel keeps blanks last in both directions.
                group, number, text, idx = sort_key(row_index)
                if direction == "desc":
                    if group == 0:
                        return (group, -number, "", idx)
                    if group == 1:
                        # Inverted ordinals make ascending tuple ordering
                        # yield a reverse-lexicographic string order.
                        inverted = "".join(chr(0xFFFF - ord(c)) for c in text)
                        return (group, 0.0, inverted, idx)
                    return (group, 0.0, "", idx)
                return (group, number, text, idx)

            sorted_rows = sorted(row_keys, key=directed_key)
            target: Dict[Tuple[int, int], Any] = {}
            for offset, source_row in enumerate(sorted_rows):
                for cell in by_row[source_row]:
                    target[(start_row + offset, int(cell["column_index"]))] = cell
            connection.execute(
                "DELETE FROM sum_cells WHERE sheet_id = ? AND row_index >= ? AND row_index < ?",
                (str(sheet_id), start_row, end_row),
            )
            connection.executemany(
                "INSERT INTO sum_cells (sheet_id, row_index, column_index, value_type, value_json, formula, style_json, revision, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(sheet_id),
                        r,
                        c,
                        cell["value_type"],
                        cell["value_json"],
                        cell["formula"],
                        cell["style_json"],
                        int(cell["revision"]),
                        int(cell["updated_at"]),
                    )
                    for (r, c), cell in target.items()
                ],
            )
            self._invalidate_sheet_indexes(connection, str(sheet_id))
            operation_id = f"sort-{time.time_ns()}"
            connection.execute(
                "INSERT INTO sum_operations (operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    user_id,
                    current_revision,
                    current_revision + 1,
                    "sort_range",
                    self._json(
                        {
                            "sheet_id": str(sheet_id),
                            "column": column,
                            "start_row": start_row,
                            "end_row": end_row,
                            "direction": direction,
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)

        revision = self._write_file_revision(
            workbook_id, user_id, "sort_range", updated
        )
        return {
            "operation_id": operation_id,
            "revision": revision,
            "rows": len(row_keys),
        }

    # ── CSV ──────────────────────────────────────────────────────────────────

    def export_csv(
        self, user_id: int, workbook_id: int, sheet_id: int
    ) -> Dict[str, Any]:
        import csv as csv_module
        import io as io_module

        self._workbook(user_id, workbook_id)
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            rows = connection.execute(
                "SELECT row_index, column_index, value_type, value_json, formula FROM sum_cells WHERE sheet_id = ? AND row_index < ? AND column_index < ? ORDER BY row_index, column_index",
                (str(sheet_id), MAX_CSV_ROWS, MAX_CSV_COLUMNS),
            ).fetchall()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file schema is invalid") from exc

        def _csv_text(value: Any) -> str:
            # Spreadsheet display convention: whole floats render without a
            # trailing ``.0`` so ``7*3`` exports as ``21``, not ``21.0``.
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)

        try:
            # Formula cells export their computed value (like a spreadsheet),
            # so round-tripping through CSV preserves the numbers users see.
            evaluator = FormulaEvaluator(self._resolver_for(connection))
            formula_cache: Dict[Tuple[int, int], Any] = {}
            grid: Dict[int, Dict[int, str]] = {}
            for cell in rows:
                row_index = int(cell["row_index"])
                column_index = int(cell["column_index"])
                if cell["value_type"] == "formula" and cell["formula"]:
                    key = (row_index, column_index)
                    if key not in formula_cache:
                        formula_cache[key] = evaluator.evaluate_cell(
                            str(sheet_id), row_index, column_index, cell["formula"]
                        )
                    value = formula_cache[key]
                    if is_error(value):
                        continue
                else:
                    value = self._decode(cell["value_json"])
                if value is None:
                    continue
                grid.setdefault(row_index, {})[column_index] = _csv_text(value)
        finally:
            self._close_sqlite(connection, path)
        max_row = max(grid) + 1 if grid else 0
        max_col = max((max(cols) for cols in grid.values()), default=-1) + 1
        buffer = io_module.StringIO()
        writer = csv_module.writer(buffer, lineterminator="\n")
        for row_index in range(max_row):
            writer.writerow(
                [grid.get(row_index, {}).get(col, "") for col in range(max_col)]
            )
        content = buffer.getvalue()
        self._audit(
            "export_csv",
            "sheet",
            sheet_id,
            user_id,
            {"workbook_id": str(workbook_id), "rows": max_row, "columns": max_col},
        )
        return {
            "workbook_id": str(workbook_id),
            "sheet_id": str(sheet_id),
            "filename": f"sheet-{sheet_id}.csv",
            "content": content,
        }

    def import_csv(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        content: str,
        base_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        import csv as csv_module
        import io as io_module

        workbook_row = self._workbook(user_id, workbook_id, mutation=True)
        if base_revision is None:
            # Match upload_file semantics: a null base revision targets the
            # current revision instead of failing the CAS check.
            base_revision = int(workbook_row["current_revision"])
        if not isinstance(content, str) or len(content) > 2_000_000:
            raise SumValidationError("CSV payload is too large")
        reader = csv_module.reader(io_module.StringIO(content))
        cells: List[Dict[str, Any]] = []
        row_count = 0
        for row_index, row in enumerate(reader):
            if row_index >= MAX_CSV_ROWS:
                break
            row_count += 1
            for column_index, value in enumerate(row):
                if column_index >= MAX_CSV_COLUMNS:
                    break
                if value == "":
                    continue
                cells.append(_classify_csv_value(row_index, column_index, value))
        if not cells:
            raise SumValidationError("CSV payload contains no cells")

        if len(cells) <= self.MAX_OPERATION_CELLS:
            # Small imports keep the single-operation path (and its
            # idempotency and per-op audit record).
            operation_id = f"csv-import-{time.time_ns()}"
            result = self.apply_operation(
                user_id,
                workbook_id,
                sheet_id,
                operation_id,
                "set_cell",
                base_revision,
                cells,
            )
            self._audit(
                "import_csv",
                "sheet",
                sheet_id,
                user_id,
                {
                    "workbook_id": str(workbook_id),
                    "operation_id": operation_id,
                    "rows": row_count,
                    "cells": len(cells),
                },
            )
            return {
                "operation_id": operation_id,
                "revision": result["revision"],
                "rows": row_count,
                "cells": len(cells),
            }

        # Large imports: apply the cells in bounded chunks inside a single
        # workbook-file transaction so a million-cell CSV costs one file
        # read + one file write instead of thousands of whole-file rewrites.
        # Each chunk is recorded as its own bounded operation entry, so the
        # operation log and replay stay intact and payloads stay bounded.
        workbook = self._workbook(user_id, workbook_id, mutation=True)
        current_revision = int(workbook["current_revision"])
        if int(base_revision) != current_revision:
            raise SumConflictError(f"Workbook revision is {current_revision}")
        self._check_range_permissions(int(workbook_id), int(sheet_id), cells, user_id)
        revision = current_revision + 1
        now = int(time.time() * 1000)
        data = self._file_bytes(workbook_id)
        operation_ids: List[str] = []
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            normalized = [
                {
                    "row": int(cell["row"]),
                    "column": int(cell["column"]),
                    "value_type": str(cell.get("value_type", "text")),
                    "value": cell.get("value"),
                    "formula": cell.get("formula"),
                    "style": cell.get("style"),
                }
                for cell in cells
            ]
            # Same enforcement as the operation path: a chunked import must
            # not bypass data-validation rules set on the target range.
            self._validate_cells(connection, str(sheet_id), normalized)
            base_ts = time.time_ns()
            for chunk_start in range(0, len(normalized), self.MAX_OPERATION_CELLS):
                chunk = normalized[chunk_start : chunk_start + self.MAX_OPERATION_CELLS]
                for cell in chunk:
                    connection.execute(
                        "INSERT INTO sum_cells (sheet_id, row_index, column_index, value_type, value_json, formula, style_json, revision, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sheet_id, row_index, column_index) DO UPDATE SET value_type = excluded.value_type, value_json = excluded.value_json, formula = excluded.formula, style_json = excluded.style_json, revision = excluded.revision, updated_at = excluded.updated_at",
                        (
                            str(sheet_id),
                            cell["row"],
                            cell["column"],
                            cell["value_type"],
                            self._json(cell["value"]),
                            cell["formula"],
                            self._json(cell["style"]),
                            revision,
                            now,
                        ),
                    )
                chunk_index = chunk_start // self.MAX_OPERATION_CELLS
                operation_id = f"csv-import-{base_ts}-{chunk_index}"
                operation_ids.append(operation_id)
                connection.execute(
                    "INSERT INTO sum_operations (operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_id,
                        user_id,
                        current_revision,
                        revision,
                        "set_cell",
                        self._json({"sheet_id": str(sheet_id), "cells": chunk}),
                        now,
                    ),
                )
            self._ensure_axis_metadata(connection, str(sheet_id), normalized)
            self._bump_statistics(connection, str(sheet_id), normalized)
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)

        new_path, size, checksum = self._publish_revision(
            int(workbook_id),
            int(current_revision),
            int(revision),
            updated,
            now,
            "Workbook revision changed during import",
        )
        self._audit(
            "import_csv",
            "sheet",
            sheet_id,
            user_id,
            {
                "workbook_id": str(workbook_id),
                "operation_id": operation_ids[0],
                "operation_count": len(operation_ids),
                "rows": row_count,
                "cells": len(cells),
            },
        )
        try:
            row = self._db.fetch_one(
                "SELECT * FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
            )
            if row:
                self._emit_update(row, revision, user_id)
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum update emit failed", exc_info=True)
        return {
            "operation_id": operation_ids[0],
            "revision": revision,
            "rows": row_count,
            "cells": len(cells),
        }

    # ── file-backed feature mutations (freeze, validation, formats, comments) ─

    def _read_file(self, user_id: int, workbook_id: int, read: Callable) -> Any:
        """Authorize, open the current file, run a read callback, close it."""
        self._workbook(user_id, workbook_id)
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            return read(connection)
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be read") from exc
        finally:
            self._close_sqlite(connection, path)

    def _mutate_file(
        self,
        user_id: int,
        workbook_id: int,
        action: str,
        mutate: Callable,
    ) -> int:
        """Open the current file, apply a mutation callback, commit a new revision.

        The callback receives the open connection and must perform its writes;
        the shared commit path bumps the revision, re-caches the bytes, prunes
        superseded blobs, audits, and emits the realtime WORKBOOK_UPDATE event.
        """
        self._workbook(user_id, workbook_id, mutation=True)
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            mutate(connection)
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)
        return self._write_file_revision(workbook_id, user_id, action, updated)

    # ── freeze panes ─────────────────────────────────────────────────────────

    def set_freeze(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        freeze_rows: int = 0,
        freeze_columns: int = 0,
    ) -> Dict[str, Any]:
        """Persist the frozen row/column pane configuration for a sheet."""
        freeze_rows = max(0, int(freeze_rows))
        freeze_columns = max(0, int(freeze_columns))
        if freeze_rows > 500 or freeze_columns > 100:
            raise SumValidationError("Freeze bounds are too large")

        def mutate(connection: sqlite3.Connection) -> None:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            connection.execute(
                "INSERT INTO sum_freeze (sheet_id, freeze_rows, freeze_columns) "
                "VALUES (?, ?, ?) ON CONFLICT(sheet_id) DO UPDATE SET "
                "freeze_rows = excluded.freeze_rows, "
                "freeze_columns = excluded.freeze_columns",
                (str(sheet_id), freeze_rows, freeze_columns),
            )

        self._mutate_file(user_id, int(workbook_id), "set_freeze", mutate)
        return self.get_freeze(user_id, int(workbook_id), int(sheet_id))

    def get_freeze(
        self, user_id: int, workbook_id: int, sheet_id: int
    ) -> Dict[str, Any]:
        """Return the frozen pane configuration for a sheet."""

        def read(connection: sqlite3.Connection) -> Dict[str, Any]:
            row = connection.execute(
                "SELECT freeze_rows, freeze_columns FROM sum_freeze WHERE sheet_id = ?",
                (str(sheet_id),),
            ).fetchone()
            return {
                "sheet_id": str(sheet_id),
                "freeze_rows": int(row["freeze_rows"]) if row else 0,
                "freeze_columns": int(row["freeze_columns"]) if row else 0,
            }

        return self._read_file(user_id, int(workbook_id), read)

    # ── data validation ──────────────────────────────────────────────────────

    VALIDATION_RULE_TYPES = ("whole", "decimal", "list", "length", "date")

    def set_validation(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        rule_type: str,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        criteria: Dict[str, Any],
        allow_blank: bool = True,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add or replace a validation rule over a range (single rule per range)."""
        rule_type = str(rule_type or "").strip()
        if rule_type not in self.VALIDATION_RULE_TYPES:
            raise SumValidationError("Unsupported validation rule type")
        row_start, row_end = int(row_start), int(row_end)
        column_start, column_end = int(column_start), int(column_end)
        if (
            row_start < 0
            or column_start < 0
            or row_end <= row_start
            or column_end <= column_start
            or (row_end - row_start) * (column_end - column_start) > 1_000_000
        ):
            raise SumValidationError("Invalid validation range")
        validation_id = self._generate_id()
        now = int(time.time() * 1000)
        criteria = dict(criteria or {})

        def mutate(connection: sqlite3.Connection) -> None:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            connection.execute(
                "DELETE FROM sum_validations WHERE sheet_id = ? AND row_start = ? "
                "AND row_end = ? AND column_start = ? AND column_end = ?",
                (str(sheet_id), row_start, row_end, column_start, column_end),
            )
            connection.execute(
                "INSERT INTO sum_validations (id, sheet_id, row_start, row_end, "
                "column_start, column_end, rule_type, criteria_json, allow_blank, "
                "message, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(validation_id),
                    str(sheet_id),
                    row_start,
                    row_end,
                    column_start,
                    column_end,
                    rule_type,
                    self._json(criteria),
                    1 if allow_blank else 0,
                    message,
                    user_id,
                    now,
                ),
            )

        self._mutate_file(user_id, int(workbook_id), "set_validation", mutate)
        return {
            "id": str(validation_id),
            "sheet_id": str(sheet_id),
            "row_start": row_start,
            "row_end": row_end,
            "column_start": column_start,
            "column_end": column_end,
            "rule_type": rule_type,
            "criteria": criteria,
            "allow_blank": bool(allow_blank),
            "message": message,
        }

    def list_validations(
        self, user_id: int, workbook_id: int, sheet_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List validation rules (optionally for one sheet)."""

        def read(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
            if sheet_id is None:
                rows = connection.execute(
                    "SELECT * FROM sum_validations ORDER BY created_at"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM sum_validations WHERE sheet_id = ? ORDER BY created_at",
                    (str(sheet_id),),
                ).fetchall()
            return [
                {
                    "id": row["id"],
                    "sheet_id": row["sheet_id"],
                    "row_start": int(row["row_start"]),
                    "row_end": int(row["row_end"]),
                    "column_start": int(row["column_start"]),
                    "column_end": int(row["column_end"]),
                    "rule_type": row["rule_type"],
                    "criteria": self._decode(row["criteria_json"], {}),
                    "allow_blank": bool(row["allow_blank"]),
                    "message": row["message"],
                    "created_at": int(row["created_at"]),
                }
                for row in rows
            ]

        return self._read_file(user_id, int(workbook_id), read)

    def delete_validation(
        self, user_id: int, workbook_id: int, validation_id: str
    ) -> None:
        """Remove a validation rule by id."""

        def mutate(connection: sqlite3.Connection) -> None:
            connection.execute(
                "DELETE FROM sum_validations WHERE id = ?", (str(validation_id),)
            )

        self._mutate_file(user_id, int(workbook_id), "delete_validation", mutate)

    def _validate_cells(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        cells: List[Dict[str, Any]],
    ) -> None:
        """Enforce data-validation rules before writes land in the file.

        Blank cells are exempt when the rule allows blanks (clearing a cell is
        always permitted). Formula cells are validated at read time instead,
        since the stored value is the formula text. ``criteria`` shapes:
        ``whole``/``decimal`` -> {min, max}; ``list`` -> {allowed: [...]};
        ``length`` -> {min, max}; ``date`` -> {after, before} (epoch ms).
        """
        rules = connection.execute(
            "SELECT * FROM sum_validations WHERE sheet_id = ?", (sheet_id,)
        ).fetchall()
        if not rules:
            return

        # Build a set of (row, col) -> cell for the cells being written
        cell_map: Dict[tuple, Dict[str, Any]] = {}
        for cell in cells:
            cell_map[(int(cell["row"]), int(cell["column"]))] = cell

        # Fetch existing cells in the affected ranges in ONE query
        if not cell_map:
            return
        rows = [r for r, c in cell_map.keys()]
        cols = [c for r, c in cell_map.keys()]
        min_row, max_row = min(rows), max(rows)
        min_col, max_col = min(cols), max(cols)

        existing = connection.execute(
            "SELECT row_index, column_index, value_type, value_json FROM sum_cells "
            "WHERE sheet_id = ? AND row_index >= ? AND row_index <= ? "
            "AND column_index >= ? AND column_index <= ?",
            (sheet_id, min_row, max_row, min_col, max_col),
        ).fetchall()

        # Merge existing cells into map
        for row in existing:
            key = (int(row["row_index"]), int(row["column_index"]))
            if key not in cell_map:
                cell_map[key] = {
                    "row": key[0],
                    "column": key[1],
                    "value_type": row["value_type"],
                    "value": json.loads(row["value_json"])
                    if row["value_json"]
                    else None,
                }

        # Validate each rule against the cell map
        for rule in rules:
            for (row_index, column_index), cell in cell_map.items():
                if cell.get("value_type") in ("blank", "__preserve__", "formula"):
                    continue
                if not (
                    int(rule["row_start"]) <= row_index < int(rule["row_end"])
                    and int(rule["column_start"])
                    <= column_index
                    < int(rule["column_end"])
                ):
                    continue
                value = cell.get("value")
                if value is None or value == "":
                    if rule["allow_blank"]:
                        continue
                    raise SumValidationError(
                        rule["message"]
                        or "This cell must not be blank (data validation)"
                    )
                criteria = self._decode(rule["criteria_json"], {})
                rule_type = rule["rule_type"]
                message = rule["message"] or "Value does not meet the validation rule"
                if rule_type == "list":
                    allowed = criteria.get("allowed", [])
                    if not (
                        value in allowed or str(value) in [str(a) for a in allowed]
                    ):
                        raise SumValidationError(message)
                elif rule_type in ("whole", "decimal"):
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        raise SumValidationError(message)
                    if rule_type == "whole" and number != int(number):
                        raise SumValidationError(message)
                    minimum = criteria.get("min")
                    maximum = criteria.get("max")
                    if minimum is not None and number < float(minimum):
                        raise SumValidationError(message)
                    if maximum is not None and number > float(maximum):
                        raise SumValidationError(message)
                elif rule_type == "length":
                    length = len(str(value))
                    minimum = criteria.get("min")
                    maximum = criteria.get("max")
                    if minimum is not None and length < int(minimum):
                        raise SumValidationError(message)
                    if maximum is not None and length > int(maximum):
                        raise SumValidationError(message)
                elif rule_type == "date":
                    after = criteria.get("after")
                    before = criteria.get("before")
                    try:
                        numeric = (
                            float(value) if isinstance(value, (int, float)) else None
                        )
                    except (TypeError, ValueError):
                        numeric = None
                    if numeric is None:
                        raise SumValidationError(message)
                    if after is not None and numeric < float(after):
                        raise SumValidationError(message)
                    if before is not None and numeric > float(before):
                        raise SumValidationError(message)

    # ── conditional formatting ───────────────────────────────────────────────

    CONDITIONAL_RULE_TYPES = (
        "cell_value",
        "text_contains",
        "formula",
        "blank",
        "duplicate",
    )

    def set_conditional_format(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        rule_type: str,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        criteria: Dict[str, Any],
        style: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Add a conditional formatting rule over a range."""
        rule_type = str(rule_type or "").strip()
        if rule_type not in self.CONDITIONAL_RULE_TYPES:
            raise SumValidationError("Unsupported conditional format rule type")
        row_start, row_end = int(row_start), int(row_end)
        column_start, column_end = int(column_start), int(column_end)
        if (
            row_start < 0
            or column_start < 0
            or row_end <= row_start
            or column_end <= column_start
        ):
            raise SumValidationError("Invalid conditional format range")
        rule_id = self._generate_id()
        now = int(time.time() * 1000)

        def mutate(connection: sqlite3.Connection) -> None:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            connection.execute(
                "INSERT INTO sum_conditional_formats (id, sheet_id, row_start, "
                "row_end, column_start, column_end, rule_type, criteria_json, "
                "style_json, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(rule_id),
                    str(sheet_id),
                    row_start,
                    row_end,
                    column_start,
                    column_end,
                    rule_type,
                    self._json(criteria),
                    self._json(style),
                    user_id,
                    now,
                ),
            )

        self._mutate_file(user_id, int(workbook_id), "set_conditional_format", mutate)
        return {
            "id": str(rule_id),
            "sheet_id": str(sheet_id),
            "row_start": row_start,
            "row_end": row_end,
            "column_start": column_start,
            "column_end": column_end,
            "rule_type": rule_type,
            "criteria": criteria,
            "style": style,
        }

    def list_conditional_formats(
        self, user_id: int, workbook_id: int, sheet_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List conditional formatting rules (optionally for one sheet)."""

        def read(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
            if sheet_id is None:
                rows = connection.execute(
                    "SELECT * FROM sum_conditional_formats ORDER BY created_at"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM sum_conditional_formats WHERE sheet_id = ? "
                    "ORDER BY created_at",
                    (str(sheet_id),),
                ).fetchall()
            return [
                {
                    "id": row["id"],
                    "sheet_id": row["sheet_id"],
                    "row_start": int(row["row_start"]),
                    "row_end": int(row["row_end"]),
                    "column_start": int(row["column_start"]),
                    "column_end": int(row["column_end"]),
                    "rule_type": row["rule_type"],
                    "criteria": self._decode(row["criteria_json"], {}),
                    "style": self._decode(row["style_json"], {}),
                }
                for row in rows
            ]

        return self._read_file(user_id, int(workbook_id), read)

    def delete_conditional_format(
        self, user_id: int, workbook_id: int, rule_id: str
    ) -> None:
        """Remove a conditional formatting rule by id."""

        def mutate(connection: sqlite3.Connection) -> None:
            connection.execute(
                "DELETE FROM sum_conditional_formats WHERE id = ?", (str(rule_id),)
            )

        self._mutate_file(
            user_id, int(workbook_id), "delete_conditional_format", mutate
        )

    # ── cell comments ────────────────────────────────────────────────────────

    def add_comment(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row_index: int,
        column_index: int,
        body: str,
    ) -> Dict[str, Any]:
        """Add a comment to a cell; @mentions generate notifications."""
        body = str(body or "").strip()
        if not body or len(body) > 5000:
            raise SumValidationError("comment must be between 1 and 5000 characters")
        row_index, column_index = int(row_index), int(column_index)
        if row_index < 0 or column_index < 0:
            raise SumValidationError("Cell coordinates are out of bounds")
        comment_id = self._generate_id()
        now = int(time.time() * 1000)
        workbook = self._workbook(user_id, int(workbook_id), mutation=True)

        def mutate(connection: sqlite3.Connection) -> None:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            connection.execute(
                "INSERT INTO sum_comments (id, sheet_id, row_index, column_index, "
                "author_id, body, resolved, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    str(comment_id),
                    str(sheet_id),
                    row_index,
                    column_index,
                    user_id,
                    body,
                    now,
                    now,
                ),
            )

        self._mutate_file(user_id, int(workbook_id), "add_comment", mutate)
        comment = {
            "id": str(comment_id),
            "sheet_id": str(sheet_id),
            "row": row_index,
            "column": column_index,
            "author_id": user_id,
            "body": body,
            "resolved": False,
            "created_at": now,
        }
        mentioned = self._notify_comment_mentions(comment, workbook)
        if mentioned:
            try:
                row: Optional[Dict[str, Any]] = self._db.fetch_one(
                    "SELECT * FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
                )
                if row:
                    self._emit_update(
                        row,
                        int(row["current_revision"]),
                        user_id,
                        {
                            "kind": "comment",
                            "comment": {
                                "id": str(comment_id),
                                "row": row_index,
                                "column": column_index,
                                "body": body[:120],
                            },
                            "mentions": mentioned,
                        },
                    )
            except Exception as exc:
                import utils.logger as logger

                logger.error(
                    f"PlexiSum could not emit comment update for workbook "
                    f"{workbook_id}: {exc}"
                )
        return comment

    def list_comments(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row: Optional[int] = None,
        column: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """List comments, optionally for a specific cell."""

        def read(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
            if row is not None and column is not None:
                rows = connection.execute(
                    "SELECT * FROM sum_comments WHERE sheet_id = ? AND row_index = ? "
                    "AND column_index = ? ORDER BY created_at",
                    (str(sheet_id), int(row), int(column)),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM sum_comments WHERE sheet_id = ? ORDER BY created_at",
                    (str(sheet_id),),
                ).fetchall()
            return [
                {
                    "id": row["id"],
                    "sheet_id": row["sheet_id"],
                    "row": int(row["row_index"]),
                    "column": int(row["column_index"]),
                    "author_id": int(row["author_id"]),
                    "body": row["body"],
                    "resolved": bool(row["resolved"]),
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                }
                for row in rows
            ]

        return self._read_file(user_id, int(workbook_id), read)

    def resolve_comment(
        self, user_id: int, workbook_id: int, comment_id: str, resolved: bool = True
    ) -> None:
        """Mark a comment resolved (or reopen it)."""

        def mutate(connection: sqlite3.Connection) -> None:
            connection.execute(
                "UPDATE sum_comments SET resolved = ?, updated_at = ? WHERE id = ?",
                (1 if resolved else 0, int(time.time() * 1000), str(comment_id)),
            )

        self._mutate_file(user_id, int(workbook_id), "resolve_comment", mutate)

    def delete_comment(self, user_id: int, workbook_id: int, comment_id: str) -> None:
        """Delete a comment."""

        def mutate(connection: sqlite3.Connection) -> None:
            connection.execute(
                "DELETE FROM sum_comments WHERE id = ?", (str(comment_id),)
            )

        self._mutate_file(user_id, int(workbook_id), "delete_comment", mutate)

    # ── XLSX import / export ─────────────────────────────────────────────────

    def export_xlsx(
        self, user_id: int, workbook_id: int, sheet_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Export the workbook (or one sheet) to an XLSX file.

        The file is produced with ``openpyxl`` in memory, base64-encoded for
        the JSON response (mirroring the ``.plexisum`` download contract),
        and formula cells are written as live formulas.
        """
        import base64 as base64_module
        import io as io_module

        self._workbook(user_id, workbook_id)
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            if sheet_id is None:
                sheets = connection.execute(
                    "SELECT id, name FROM sum_sheets WHERE deleted = 0 ORDER BY position"
                ).fetchall()
            else:
                sheet_row = connection.execute(
                    "SELECT id, name FROM sum_sheets WHERE id = ? AND deleted = 0",
                    (str(sheet_id),),
                ).fetchone()
                if sheet_row is None:
                    raise SumNotFoundError("Sheet not found")
                sheets = [sheet_row]
            rows = connection.execute(
                "SELECT sheet_id, row_index, column_index, value_type, value_json, formula "
                "FROM sum_cells WHERE row_index < ? AND column_index < ? ORDER BY row_index, column_index",
                (MAX_CSV_ROWS, MAX_CSV_COLUMNS),
            ).fetchall()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file schema is invalid") from exc

        try:
            import openpyxl
            from openpyxl.cell.cell import MergedCell

            wb = openpyxl.Workbook()
            ws = wb.active
            if ws is None:
                ws = wb.create_sheet()
            for sheet_index, sheet in enumerate(sheets):
                name = str(sheet["name"] or f"Sheet {sheet_index + 1}")
                if sheet_index > 0:
                    ws = wb.create_sheet()
                ws.title = name[:31]
                for cell in rows:
                    if str(cell["sheet_id"]) != str(sheet["id"]):
                        continue
                    row_index = int(cell["row_index"]) + 1
                    column_index = int(cell["column_index"]) + 1
                    target_cell = ws.cell(row=row_index, column=column_index)
                    if target_cell is None or isinstance(target_cell, MergedCell):
                        continue
                    if cell["value_type"] == "formula" and cell["formula"]:
                        target_cell.value = f"={cell['formula']}"
                    else:
                        value = self._decode(cell["value_json"])
                        if value is not None:
                            target_cell.value = value
            buffer = io_module.BytesIO()
            wb.save(buffer)
            content = base64_module.b64encode(buffer.getvalue()).decode("ascii")
        except ImportError:
            raise SumValidationError(
                "XLSX support requires openpyxl; it is not installed"
            )
        finally:
            self._close_sqlite(connection, path)
        self._audit(
            "export_xlsx",
            "workbook",
            workbook_id,
            user_id,
            {"sheets": len(sheets)},
        )
        return {
            "workbook_id": str(workbook_id),
            "filename": f"{self.get_workbook(user_id, workbook_id)['title'] or 'workbook'}.xlsx",
            "content": content,
            "size": len(base64_module.b64decode(content)),
        }

    def import_xlsx(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        content_b64: str,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Import an XLSX file's first sheet into a workbook sheet as cells.

        Values are classified with the same inference used for CSV (numbers,
        booleans, text), and the import is applied through the chunked bulk
        path so a single file rewrite carries the whole payload.
        """
        import base64 as base64_module
        import io as io_module

        self._workbook(user_id, workbook_id, mutation=True)
        if not isinstance(content_b64, str) or not content_b64:
            raise SumValidationError("XLSX content is required")
        try:
            raw = base64_module.b64decode(content_b64)
        except Exception:
            raise SumValidationError("XLSX content is not valid base64")
        if len(raw) > 20 * 1024 * 1024:
            raise SumValidationError("XLSX file is too large")
        try:
            import openpyxl

            wb = openpyxl.load_workbook(
                io_module.BytesIO(raw), read_only=True, data_only=False
            )
            ws = wb.active
            if ws is None:
                raise SumValidationError("Workbook has no active sheet")
        except ImportError:
            raise SumValidationError(
                "XLSX support requires openpyxl; it is not installed"
            )
        except Exception:
            raise SumValidationError("File is not a valid XLSX workbook")
        cells: List[Dict[str, Any]] = []
        row_count = 0
        for row_index, row in enumerate(ws.iter_rows()):
            if row_index >= MAX_CSV_ROWS:
                break
            row_count += 1
            for column_index, cell in enumerate(row):
                if column_index >= MAX_CSV_COLUMNS:
                    break
                value = cell.value
                if value is None or value == "":
                    continue
                if cell.data_type == "f" and isinstance(value, str):
                    formula = value[1:] if value.startswith("=") else value
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "value_type": "formula",
                            "value": None,
                            "formula": formula,
                        }
                    )
                    continue
                if isinstance(value, bool):
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "value_type": "boolean",
                            "value": value,
                        }
                    )
                elif isinstance(value, (int, float)):
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "value_type": "number",
                            "value": value,
                        }
                    )
                else:
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "value_type": "text",
                            "value": str(value),
                        }
                    )
        if not cells:
            raise SumValidationError("XLSX contains no cells to import")
        if len(cells) <= self.MAX_OPERATION_CELLS:
            operation_id = f"xlsx-import-{time.time_ns()}"
            result = self.apply_operation(
                user_id,
                workbook_id,
                sheet_id,
                operation_id,
                "set_cell",
                base_revision,
                cells,
            )
            self._audit(
                "import_xlsx",
                "sheet",
                sheet_id,
                user_id,
                {"operation_id": operation_id, "rows": row_count, "cells": len(cells)},
            )
            return {
                "operation_id": operation_id,
                "revision": result["revision"],
                "rows": row_count,
                "cells": len(cells),
            }
        # Reuse the chunked CSV bulk path: it is already a single-file
        # transaction that records bounded operation entries per chunk.
        result = self.import_csv(
            user_id,
            workbook_id,
            sheet_id,
            self._cells_to_csv(cells),
            base_revision,
        )
        return {
            "operation_id": result["operation_id"],
            "revision": result["revision"],
            "rows": row_count,
            "cells": len(cells),
        }

    @staticmethod
    def _cells_to_csv(cells: List[Dict[str, Any]]) -> str:
        """Render a flat cell list to a minimal CSV grid for the bulk importer."""
        import csv as csv_module
        import io as io_module

        grid: Dict[int, Dict[int, Any]] = {}
        for cell in cells:
            grid.setdefault(int(cell["row"]), {})[int(cell["column"])] = (
                f"={cell['formula']}"
                if cell.get("value_type") == "formula" and cell.get("formula")
                else cell.get("value")
            )
        buffer = io_module.StringIO()
        writer = csv_module.writer(buffer, lineterminator="\n")
        max_row = max(grid) + 1 if grid else 0
        max_col = max((max(cols) for cols in grid.values()), default=-1) + 1
        for row_index in range(max_row):
            writer.writerow(
                [grid.get(row_index, {}).get(col, "") for col in range(max_col)]
            )
        return buffer.getvalue()

    # ── sharing / grants (main-db ACL) ───────────────────────────────────────

    def grant_workbook(
        self,
        user_id: int,
        workbook_id: int,
        subject_id: int,
        permissions: List[str],
        expires_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        if not isinstance(permissions, list) or not permissions:
            raise SumValidationError("permissions must be a non-empty list")
        allowed = {"view", "edit"}
        unknown = set(permissions) - allowed
        if unknown:
            raise SumValidationError(f"Unknown permissions: {sorted(unknown)}")
        now = int(time.time() * 1000)
        if expires_at is not None and int(expires_at) <= now:
            raise SumValidationError("expires_at must be in the future")
        grant_id = self._generate_id()
        self._db.execute(
            "INSERT INTO sum_grants (id, workbook_id, subject_id, permissions, expires_at, revoked_at, created_by, created_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                grant_id,
                workbook_id,
                int(subject_id),
                self._json(sorted(set(permissions))),
                int(expires_at) if expires_at is not None else None,
                user_id,
                now,
            ),
        )
        self._audit(
            "grant",
            "workbook",
            workbook_id,
            user_id,
            {
                "grant_id": str(grant_id),
                "subject_id": int(subject_id),
                "permissions": sorted(set(permissions)),
            },
        )
        return {
            "id": str(grant_id),
            "workbook_id": str(workbook_id),
            "subject_id": str(subject_id),
            "permissions": sorted(set(permissions)),
            "expires_at": int(expires_at) if expires_at is not None else None,
            "created_at": now,
        }

    def revoke_grant(self, user_id: int, grant_id: int) -> None:
        row = self._db.fetch_one(
            "SELECT workbook_id FROM sum_grants WHERE id = ? AND revoked_at IS NULL",
            (int(grant_id),),
        )
        if not row:
            raise SumNotFoundError("Grant not found")
        self._workbook(user_id, int(row["workbook_id"]), mutation=True)
        now = int(time.time() * 1000)
        self._db.execute(
            "UPDATE sum_grants SET revoked_at = ? WHERE id = ?", (now, int(grant_id))
        )
        self._audit(
            "revoke_grant",
            "workbook",
            int(row["workbook_id"]),
            user_id,
            {"grant_id": str(grant_id)},
        )

    def list_grants(self, user_id: int, workbook_id: int) -> List[Dict[str, Any]]:
        self._workbook(user_id, workbook_id)
        rows = self._db.fetch_all(
            "SELECT id, subject_id, permissions, expires_at, revoked_at, created_at FROM sum_grants WHERE workbook_id = ? ORDER BY created_at ASC",
            (int(workbook_id),),
        )
        return [
            {
                "id": str(row["id"]),
                "workbook_id": str(workbook_id),
                "subject_id": str(row["subject_id"]),
                "permissions": self._decode(row.get("permissions"), []),
                "expires_at": int(row["expires_at"])
                if row.get("expires_at") is not None
                else None,
                "revoked_at": int(row["revoked_at"])
                if row.get("revoked_at") is not None
                else None,
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    # ── persisted client features (charts / pivots / sparklines) ─────────────

    SUM_FEATURE_TYPES = ("chart", "pivot", "sparkline")
    SUM_FEATURE_MAX_DEFINITION_BYTES = 300 * 1024

    def _feature_dict(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(row["id"]),
            "workbook_id": str(row["workbook_id"]),
            "feature_type": row["feature_type"],
            "sheet_id": row.get("sheet_id"),
            "definition": self._decode(row.get("definition_json"), {}),
            "created_by": str(row["created_by"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def _feature_row(self, workbook_id: int, feature_id: int) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM sum_features WHERE id = ? AND workbook_id = ?",
            (int(feature_id), int(workbook_id)),
        )
        if not row:
            raise SumNotFoundError("Workbook feature not found")
        return row

    def create_feature(
        self,
        user_id: int,
        workbook_id: int,
        feature_type: str,
        definition: Dict[str, Any],
        sheet_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        feature_type = str(feature_type or "").strip().lower()
        if feature_type not in self.SUM_FEATURE_TYPES:
            raise SumValidationError("Unknown workbook feature type")
        if not isinstance(definition, dict):
            raise SumValidationError("feature definition must be an object")
        encoded = self._json(definition, {})
        if len(encoded) > self.SUM_FEATURE_MAX_DEFINITION_BYTES:
            raise SumValidationError("feature definition is too large")
        feature_id = self._generate_id()
        now = int(time.time() * 1000)
        self._db.execute(
            "INSERT INTO sum_features (id, workbook_id, feature_type, sheet_id, definition_json, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                feature_id,
                int(workbook_id),
                feature_type,
                str(sheet_id) if sheet_id is not None else None,
                encoded,
                user_id,
                now,
                now,
            ),
        )
        self._audit(
            "create_feature",
            "workbook",
            int(workbook_id),
            user_id,
            {"feature_type": feature_type, "feature_id": str(feature_id)},
        )
        return self._feature_dict(self._feature_row(workbook_id, feature_id))

    def get_feature(
        self, user_id: int, workbook_id: int, feature_id: int
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id)
        return self._feature_dict(self._feature_row(workbook_id, feature_id))

    def list_features(
        self,
        user_id: int,
        workbook_id: int,
        feature_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self._workbook(user_id, workbook_id)
        if feature_type is not None:
            feature_type = str(feature_type).strip().lower()
            if feature_type not in self.SUM_FEATURE_TYPES:
                raise SumValidationError("Unknown workbook feature type")
            rows = self._db.fetch_all(
                "SELECT * FROM sum_features WHERE workbook_id = ? AND feature_type = ? ORDER BY created_at ASC",
                (int(workbook_id), feature_type),
            )
        else:
            rows = self._db.fetch_all(
                "SELECT * FROM sum_features WHERE workbook_id = ? ORDER BY created_at ASC",
                (int(workbook_id),),
            )
        return [self._feature_dict(row) for row in rows]

    def update_feature(
        self,
        user_id: int,
        workbook_id: int,
        feature_id: int,
        *,
        definition: Optional[Dict[str, Any]] = None,
        sheet_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        self._feature_row(workbook_id, feature_id)
        now = int(time.time() * 1000)
        if definition is not None:
            if not isinstance(definition, dict):
                raise SumValidationError("feature definition must be an object")
            encoded = self._json(definition, {})
            if len(encoded) > self.SUM_FEATURE_MAX_DEFINITION_BYTES:
                raise SumValidationError("feature definition is too large")
            self._db.execute(
                "UPDATE sum_features SET definition_json = ?, updated_at = ? WHERE id = ?",
                (encoded, now, int(feature_id)),
            )
        if sheet_id is not None:
            self._db.execute(
                "UPDATE sum_features SET sheet_id = ?, updated_at = ? WHERE id = ?",
                (str(sheet_id), now, int(feature_id)),
            )
        self._audit(
            "update_feature",
            "workbook",
            int(workbook_id),
            user_id,
            {"feature_id": str(feature_id)},
        )
        return self._feature_dict(self._feature_row(workbook_id, feature_id))

    def delete_feature(self, user_id: int, workbook_id: int, feature_id: int) -> None:
        self._workbook(user_id, workbook_id, mutation=True)
        self._feature_row(workbook_id, feature_id)
        self._db.execute(
            "DELETE FROM sum_features WHERE id = ? AND workbook_id = ?",
            (int(feature_id), int(workbook_id)),
        )
        self._audit(
            "delete_feature",
            "workbook",
            int(workbook_id),
            user_id,
            {"feature_id": str(feature_id)},
        )

    # ── range permissions (main-db ACL) ──────────────────────────────────────

    def set_range_permission(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        permission: str,
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        self._ensure_sheet(self._file_bytes(workbook_id), str(sheet_id))
        if permission not in ("read_only", "editable"):
            raise SumValidationError("permission must be read_only or editable")
        row_start, row_end = int(row_start), int(row_end)
        column_start, column_end = int(column_start), int(column_end)
        if (
            row_start < 0
            or column_start < 0
            or row_end <= row_start
            or column_end <= column_start
        ):
            raise SumValidationError("Invalid range bounds")
        if (row_end - row_start) * (column_end - column_start) > 1_000_000:
            raise SumValidationError("Range is too large")
        now = int(time.time() * 1000)
        existing = self._db.fetch_one(
            "SELECT id FROM sum_range_permissions WHERE workbook_id = ? AND sheet_id = ? AND row_start = ? AND row_end = ? AND column_start = ? AND column_end = ?",
            (
                int(workbook_id),
                int(sheet_id),
                row_start,
                row_end,
                column_start,
                column_end,
            ),
        )
        if existing:
            self._db.execute(
                "UPDATE sum_range_permissions SET permission = ?, created_by = ?, created_at = ? WHERE id = ?",
                (permission, user_id, now, int(existing["id"])),
            )
            range_id = int(existing["id"])
        else:
            range_id = self._generate_id()
            self._db.execute(
                "INSERT INTO sum_range_permissions (id, workbook_id, sheet_id, row_start, row_end, column_start, column_end, permission, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    range_id,
                    int(workbook_id),
                    int(sheet_id),
                    row_start,
                    row_end,
                    column_start,
                    column_end,
                    permission,
                    user_id,
                    now,
                ),
            )
        self._audit(
            "set_range_permission",
            "sheet",
            sheet_id,
            user_id,
            {
                "workbook_id": str(workbook_id),
                "range_id": str(range_id),
                "permission": permission,
                "range": [row_start, row_end, column_start, column_end],
            },
        )
        return {
            "id": str(range_id),
            "workbook_id": str(workbook_id),
            "sheet_id": str(sheet_id),
            "row_start": row_start,
            "row_end": row_end,
            "column_start": column_start,
            "column_end": column_end,
            "permission": permission,
            "created_at": now,
        }

    def list_range_permissions(
        self, user_id: int, workbook_id: int, sheet_id: int
    ) -> List[Dict[str, Any]]:
        self._workbook(user_id, workbook_id)
        self._ensure_sheet(self._file_bytes(workbook_id), str(sheet_id))
        rows = self._db.fetch_all(
            "SELECT id, row_start, row_end, column_start, column_end, permission, created_at FROM sum_range_permissions WHERE workbook_id = ? AND sheet_id = ? ORDER BY created_at ASC",
            (int(workbook_id), int(sheet_id)),
        )
        return [
            {
                "id": str(row["id"]),
                "workbook_id": str(workbook_id),
                "sheet_id": str(sheet_id),
                "row_start": int(row["row_start"]),
                "row_end": int(row["row_end"]),
                "column_start": int(row["column_start"]),
                "column_end": int(row["column_end"]),
                "permission": row["permission"],
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def remove_range_permission(
        self, user_id: int, workbook_id: int, range_id: int
    ) -> None:
        row = self._db.fetch_one(
            "SELECT id, sheet_id FROM sum_range_permissions WHERE id = ? AND workbook_id = ?",
            (int(range_id), int(workbook_id)),
        )
        if not row:
            raise SumNotFoundError("Range permission not found")
        self._workbook(user_id, workbook_id, mutation=True)
        self._db.execute(
            "DELETE FROM sum_range_permissions WHERE id = ?", (int(range_id),)
        )
        self._audit(
            "remove_range_permission",
            "sheet",
            int(row["sheet_id"]),
            user_id,
            {"workbook_id": str(workbook_id), "range_id": str(range_id)},
        )

    # ── account erasure ──────────────────────────────────────────────────────

    def purge_workbook_by_id(
        self, workbook_id: int, purge_artifact: bool = True
    ) -> bool:
        """Permanently erase one workbook and all owned storage references.

        ``purge_artifact`` is disabled when the caller is already deleting the
        linked Artifact row; keeping that distinction prevents recursive
        cleanup while making this direct lifecycle entry point complete.
        """
        row = self._db.fetch_one(
            "SELECT id, owner_id, blob_path, current_revision, artifact_id "
            "FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        if not row:
            return False
        self._purge_workbook_rows([row], purge_artifacts=purge_artifact)
        _WORKBOOK_FILE_CACHE.drop_workbook(int(workbook_id))
        return True

    def _purge_workbook_rows(
        self, workbooks: List[Dict[str, Any]], purge_artifacts: bool = False
    ) -> None:
        """Delete workbook blobs, linked metadata, and optionally artifacts."""
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError("PlexiSum purge requires a standalone transaction")
        workbook_ids = [int(row["id"]) for row in workbooks]
        # Keep a bounded-in-scope backup of existing blobs until the metadata
        # transaction commits. If a database/artifact delete fails after the
        # external delete, restore the exact immutable objects before re-raising.
        # This prevents rows that survived rollback from pointing at missing
        # workbook files.
        backups: Dict[str, bytes] = {}
        backup_bytes = 0
        try:
            for wb in workbooks:
                wb_id = int(wb["id"])
                revision_rows = self._db.fetch_all(
                    "SELECT blob_path FROM sum_revision_blobs WHERE workbook_id = ?",
                    (wb_id,),
                )
                paths = [
                    str(item["blob_path"])
                    for item in revision_rows
                    if item.get("blob_path")
                ]
                current_path = wb.get("blob_path")
                if current_path:
                    paths.append(str(current_path))
                snapshots = self._db.fetch_all(
                    "SELECT blob_path FROM sum_snapshots WHERE workbook_id = ?",
                    (wb_id,),
                )
                paths.extend(
                    str(item["blob_path"])
                    for item in snapshots
                    if item.get("blob_path")
                )
                if not revision_rows and current_path:
                    try:
                        current = int(wb.get("current_revision") or 0)
                    except (TypeError, ValueError):
                        current = 0
                    directory = str(current_path).rsplit("/", 1)[0]
                    paths.extend(
                        f"{directory}/rev-{revision}.plexisum"
                        for revision in range(current + 1)
                    )
                for path in dict.fromkeys(paths):
                    try:
                        if self._blob.exists(path):
                            data = self._blob.retrieve(path)
                            if backup_bytes + len(data) > MAX_PURGE_BACKUP_BYTES:
                                raise SumStorageError(
                                    "PlexiSum purge backup exceeds the safety limit"
                                )
                            backups[path] = data
                            backup_bytes += len(data)
                    except Exception as exc:
                        raise SumStorageError(
                            f"Could not read PlexiSum blob before purge: {path}"
                        ) from exc
                failed = [
                    path
                    for path in dict.fromkeys(paths)
                    if not self._delete_blob_confirmed(path)
                ]
                if failed:
                    raise SumStorageError(
                        "Could not confirm deletion of PlexiSum blobs: "
                        + ", ".join(failed[:10])
                    )
        except Exception:
            restore_failures = []
            for path, data in backups.items():
                try:
                    if not self._blob.exists(path):
                        self._blob.store(data, path)
                except Exception:
                    restore_failures.append(path)
            if restore_failures:
                import utils.logger as logger

                logger.error(
                    "PlexiSum purge could not restore blobs after storage cleanup failure: %s",
                    ", ".join(restore_failures[:10]),
                )
            raise
        try:
            self._db.begin_transaction()
            for wb_id in workbook_ids:
                # Remove the linked artifact inside the same transaction before
                # workbook metadata. If a later SQL step fails, rollback keeps
                # both metadata and artifact rows retryable; external blobs were
                # already confirmed absent above.
                artifact_row = self._db.fetch_one(
                    "SELECT artifact_id FROM sum_workbooks WHERE id = ?", (wb_id,)
                )
                if (
                    purge_artifacts
                    and artifact_row
                    and artifact_row.get("artifact_id") is not None
                ):
                    from src.core.artifacts.repository import delete_artifact

                    delete_artifact(self._db, int(artifact_row["artifact_id"]))
                self._db.execute(
                    "DELETE FROM sum_range_permissions WHERE workbook_id = ?", (wb_id,)
                )
                self._db.execute(
                    "DELETE FROM sum_grants WHERE workbook_id = ?", (wb_id,)
                )
                self._db.execute(
                    "DELETE FROM sum_snapshots WHERE workbook_id = ?", (wb_id,)
                )
                self._db.execute(
                    "DELETE FROM sum_revision_blobs WHERE workbook_id = ?", (wb_id,)
                )
                self._db.execute(
                    "DELETE FROM sum_operations WHERE workbook_id = ?", (wb_id,)
                )
                self._db.execute("DELETE FROM sum_workbooks WHERE id = ?", (wb_id,))
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            restore_failures = []
            for path, data in backups.items():
                try:
                    if not self._blob.exists(path):
                        self._blob.store(data, path)
                except Exception:
                    restore_failures.append(path)
            if restore_failures:
                import utils.logger as logger

                logger.error(
                    "PlexiSum purge could not restore blobs after DB rollback: %s",
                    ", ".join(restore_failures[:10]),
                )
            raise
        # Any live sessions for purged workbooks must be discarded.
        for wb_id in workbook_ids:
            with self._session_lock(int(wb_id)):
                self._drop_session(int(wb_id))

    def purge_user_data(self, user_id: int) -> None:
        """Permanently erase a user's workbook files and metadata.

        Workbook content lives in the blob store, not the main database, so
        erasure must remove both the immutable versioned blobs and the
        metadata rows. Because every mutation writes a new immutable
        ``rev-N`` blob while ``sum_workbooks.blob_path`` only tracks the
        current one, this erases every revision blob (rev-0 through
        ``current_revision``) plus all snapshot blobs to avoid orphaned
        objects on the storage adapter. It also revokes grants that point at
        the user as a subject on other people's workbooks, and it is
        idempotent and tolerant of a user who never created workbooks.
        """
        # Blob deletion is irreversible from the database's point of view.
        # Reject an outer transaction before touching storage, otherwise a
        # later caller rollback could leave metadata pointing at deleted files.
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError("PlexiSum purge requires a standalone transaction")
        uid = int(user_id)
        workbooks = self._db.fetch_all(
            "SELECT id, artifact_id, blob_path, current_revision "
            "FROM sum_workbooks WHERE owner_id = ?",
            (uid,),
        )
        self._purge_workbook_rows(workbooks, purge_artifacts=True)
        try:
            self._db.begin_transaction()
            self._db.execute("DELETE FROM sum_grants WHERE subject_id = ?", (uid,))
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            raise
        _WORKBOOK_FILE_CACHE.clear()
        return
