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
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.base import BaseManager

from .formulas import FormulaEvaluator, is_error
from .schema import (
    SUM_OPERATION_TYPES,
    SUM_VALUE_TYPES,
    ensure_feature_tables,
)

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

# Keep this many immutable ``rev-N`` blobs per workbook after a successful
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
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        return key

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
            pass
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
                pass
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

    # ── workbook file lifecycle ──────────────────────────────────────────────

    def _file_bytes(self, workbook_id: int) -> bytes:
        """Retrieve the current workbook file bytes, cached by revision.

        Blobs are immutable and the cache key includes the revision, so a hit
        can never be stale. This removes the repeated full-file storage
        downloads that previously happened on every viewport read, replay,
        export, and write.
        """
        row = self._db.fetch_one(
            "SELECT blob_path, current_revision FROM sum_workbooks "
            "WHERE id = ? AND deleted = 0",
            (int(workbook_id),),
        )
        if not row or not row.get("blob_path"):
            raise SumStorageError("Workbook file is not available")
        revision = int(row.get("current_revision") or 0)
        cached = _WORKBOOK_FILE_CACHE.get((int(workbook_id), revision))
        if cached is not None:
            return cached
        try:
            data = self._blob.retrieve(str(row["blob_path"]))
        except Exception as exc:
            raise SumStorageError("Workbook file could not be read") from exc
        _WORKBOOK_FILE_CACHE.put((int(workbook_id), revision), data)
        return data

    def _cache_current_file(self, workbook_id: int, revision: int, data: bytes) -> None:
        """Publish just-committed file bytes to the read cache."""
        _WORKBOOK_FILE_CACHE.put((int(workbook_id), int(revision)), data)

    def _prune_revision_blobs(self, workbook_id: int) -> None:
        """Best-effort GC of superseded immutable revision blobs.

        Every mutation writes a new immutable ``rev-N`` blob while the
        metadata row only tracks the current one, so older revision blobs are
        never referenced by any read path and would otherwise grow storage by
        the full file size on every edit. Keep a small window for crash
        tolerance and delete the rest.
        """
        try:
            current_row = self._db.fetch_one(
                "SELECT blob_path, current_revision FROM sum_workbooks WHERE id = ?",
                (int(workbook_id),),
            )
            if not current_row or not current_row.get("blob_path"):
                return
            directory = str(current_row["blob_path"]).rsplit("/", 1)[0]
            current = int(current_row.get("current_revision") or 0)
        except Exception:
            return
        for revision in range(max(0, current - REVISION_KEEP_COUNT)):
            try:
                self._blob.delete(f"{directory}/rev-{revision}.plexisum")
            except Exception:
                pass

    def _save_file(
        self, workbook_id: int, revision: int, data: bytes
    ) -> Tuple[str, int, str]:
        """Store a new immutable versioned blob; return (path, size, checksum)."""
        key = f"{int(workbook_id)}/rev-{int(revision)}.plexisum"
        path = self._blob.store(data, key)
        return path, len(data), hashlib.sha256(data).hexdigest()

    @staticmethod
    def _open_sqlite(data: bytes) -> Tuple[sqlite3.Connection, str]:
        """Open workbook bytes as a sqlite connection over a temp file.

        Feature tables (validations, conditional formats, comments, freeze)
        are backfilled idempotently on every open so older ``.plexisum``
        files created before those tables shipped work transparently.
        """
        handle = tempfile.NamedTemporaryFile(
            prefix="plexisum-", suffix=".plexisum", delete=False
        )
        handle.write(data)
        handle.close()
        connection = sqlite3.connect(handle.name)
        connection.row_factory = sqlite3.Row
        try:
            ensure_feature_tables(connection)
        except sqlite3.Error:
            pass
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

    def _read_sheets_from_file(self, data: bytes) -> List[Dict[str, Any]]:
        connection, path = self._open_sqlite(data)
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
        except sqlite3.Error:
            pass
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
    ) -> Dict[str, Any]:
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
            from .schema import create_workbook_file

            create_workbook_file(handle.name, workbook_id=workbook_id)
            connection = sqlite3.connect(handle.name)
            try:
                connection.execute(
                    "INSERT INTO sum_sheets (id, name, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (str(sheet_id), "Sheet 1", 0, now, now),
                )
                connection.commit()
            finally:
                connection.close()
            with open(handle.name, "rb") as file_handle:
                data = file_handle.read()
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

        path, size, checksum = self._save_file(workbook_id, 0, data)
        self._db.begin_transaction()
        try:
            self._db.execute(
                "INSERT INTO sum_workbooks (id, owner_id, title, scope, server_id, current_revision, blob_path, file_size, checksum, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workbook_id,
                    user_id,
                    title,
                    scope,
                    server_id,
                    0,
                    path,
                    size,
                    checksum,
                    now,
                    now,
                ),
            )
            self._audit(
                "create",
                "workbook",
                workbook_id,
                user_id,
                {"title": title, "scope": scope},
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            self._blob.delete(path)
            raise
        self._cache_current_file(workbook_id, 0, data)
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
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def list_workbooks(
        self, user_id: int, limit: int = 50, offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        # In addition to owned and server-scope workbooks, include workbooks
        # shared with this user through a live grant (view or edit).
        now = int(time.time() * 1000)
        where = (
            "deleted = 0 AND (owner_id = ? OR "
            "(scope = 'server' AND server_id IN "
            "(SELECT server_id FROM srv_members WHERE user_id = ?)) OR "
            "EXISTS (SELECT 1 FROM sum_grants g "
            "WHERE g.workbook_id = sum_workbooks.id AND g.subject_id = ? "
            "AND g.revoked_at IS NULL "
            "AND (g.expires_at IS NULL OR g.expires_at > ?)))"
        )
        params = (user_id, user_id, user_id, now)
        count = self._db.fetch_one(
            f"SELECT COUNT(*) AS count FROM sum_workbooks WHERE {where}", params
        )
        rows = self._db.fetch_all(
            f"SELECT * FROM sum_workbooks WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params + (limit, offset),
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
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                }
            )
        return items, int(count["count"]) if count else 0

    def delete_workbook(self, user_id: int, workbook_id: int) -> None:
        self._workbook(user_id, workbook_id, mutation=True)
        now = int(time.time() * 1000)
        self._db.execute(
            "UPDATE sum_workbooks SET deleted = 1, updated_at = ? WHERE id = ?",
            (now, int(workbook_id)),
        )
        self._audit("delete", "workbook", int(workbook_id), user_id)

    def restore_workbook(self, user_id: int, workbook_id: int) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM sum_workbooks WHERE id = ? AND deleted = 1",
            (int(workbook_id),),
        )
        if not row:
            raise SumNotFoundError("Deleted workbook not found")
        self._authorize(row, user_id, mutation=True)
        now = int(time.time() * 1000)
        self._db.execute(
            "UPDATE sum_workbooks SET deleted = 0, updated_at = ? WHERE id = ?",
            (now, int(workbook_id)),
        )
        self._audit("restore", "workbook", int(workbook_id), user_id)
        return self.get_workbook(user_id, workbook_id)

    # ── file download / upload ───────────────────────────────────────────────

    def download_file(self, user_id: int, workbook_id: int) -> Dict[str, Any]:
        """Return the current workbook file for authorized download."""
        row = self._workbook(user_id, workbook_id)
        data = self._file_bytes(workbook_id)
        self._audit(
            "download",
            "workbook_file",
            int(workbook_id),
            user_id,
            {"size": len(data)},
        )
        # Base64-encode the bytes: a .plexisum file is a binary SQLite
        # database and raw bytes are not reliably valid UTF-8, so embedding
        # them verbatim in a JSON string would 500 on arbitrary binary data.
        # Base64 also mirrors the upload contract.
        return {
            "workbook_id": str(workbook_id),
            "filename": f"{row['title'] or 'workbook'}.plexisum",
            "content": base64.b64encode(data).decode("ascii"),
            "size": len(data),
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
        current = self._db.fetch_one(
            "SELECT current_revision FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        base = current["current_revision"] if current else 0
        if base_revision is not None and int(base_revision) != int(base):
            raise SumConflictError(f"Workbook revision is {base}")
        revision = int(base) + 1
        now = int(time.time() * 1000)
        path, size, checksum = self._save_file(workbook_id, revision, data)
        cursor = self._db.execute(
            "UPDATE sum_workbooks SET current_revision = ?, blob_path = ?, file_size = ?, checksum = ?, updated_at = ? WHERE id = ? AND current_revision = ?",
            (revision, path, size, checksum, now, workbook_id, base),
        )
        try:
            affected = int(cursor.rowcount)
        finally:
            cursor.close()
        if affected != 1:
            self._blob.delete(path)
            raise SumConflictError("Workbook revision changed during upload")
        self._cache_current_file(workbook_id, revision, data)
        self._prune_revision_blobs(workbook_id)
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
        """Reject bytes that are not a valid PlexiSum workbook file."""
        from .schema import PLEXISUM_FORMAT_VERSION

        if not data.startswith(b"SQLite format 3"):
            raise SumValidationError("File is not a SQLite database")
        connection, path = self._open_sqlite(data)
        try:
            meta = connection.execute(
                "SELECT value FROM sum_meta WHERE key = 'format_version'"
            ).fetchone()
            if meta is None or meta["value"] != PLEXISUM_FORMAT_VERSION:
                raise SumValidationError("File is not a supported PlexiSum workbook")
            connection.execute("SELECT id FROM sum_sheets LIMIT 1").fetchall()
        except sqlite3.Error as exc:
            raise SumValidationError("File is not a valid PlexiSum workbook") from exc
        finally:
            self._close_sqlite(connection, path)

    # ── viewport reads ───────────────────────────────────────────────────────

    # ── viewport reads ───────────────────────────────────────────────────────

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
        except sqlite3.Error:
            pass
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
        except sqlite3.Error:
            pass
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
        except sqlite3.Error:
            pass
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
        except sqlite3.Error:
            pass
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
                "SELECT row_index, column_index, value_type, value_json, formula, style_json, revision FROM sum_cells WHERE sheet_id = ? AND row_index >= ? AND row_index < ? AND column_index >= ? AND column_index < ? ORDER BY row_index, column_index",
                (str(sheet_id), row_start, row_end, column_start, column_end),
            ).fetchall()
            used = connection.execute(
                "SELECT MAX(row_index) AS max_row, MAX(column_index) AS max_col FROM sum_cells WHERE sheet_id = ?",
                (str(sheet_id),),
            ).fetchone()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file schema is invalid") from exc

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
                formula_cells.append((cell_data["row"], cell_data["column"], cell_data))
            cells.append(cell_data)

        if formula_cells:
            # Formulas are evaluated on read against the same file, so the
            # viewport returns live computed values (bounded, cycle-safe).
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

        # Server-side filtering: rows that fail every criterion are reported as
        # hidden. Cells for hidden rows are still returned so the client can
        # restore them instantly when the filter is cleared; the UI greys out
        # hidden rows instead of deleting data.
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
        self._close_sqlite(connection, path)
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

        # Idempotency wins over revision staleness: a client retrying an op
        # it already applied (possibly against a newer revision) must receive
        # the original result rather than a conflict.
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            existing = connection.execute(
                "SELECT result_revision FROM sum_operations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
        finally:
            self._close_sqlite(connection, path)
        if existing is not None:
            return {
                "operation_id": operation_id,
                "revision": int(existing["result_revision"]),
                "replayed": True,
            }

        if int(base_revision) != current_revision:
            raise SumConflictError(f"Workbook revision is {current_revision}")

        if operation_type in STRUCTURAL_OPERATION_TYPES:
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

        self._check_range_permissions(
            int(workbook_id), int(sheet_id), normalized, user_id
        )

        # ``data`` was already fetched for the idempotency check above; a
        # second storage download is unnecessary and would double the cost of
        # every edit for a hot workbook.
        revision = current_revision + 1
        now = int(time.time() * 1000)

        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            # Data-validation rules are enforced on the write path against
            # the live workbook file, so invalid values never land in the
            # file regardless of which client sent them.
            self._validate_cells(connection, str(sheet_id), normalized)
            for cell in normalized:
                row_index = cell["row"]
                column_index = cell["column"]
                if cell["value_type"] == "__preserve__":
                    previous = connection.execute(
                        "SELECT value_type, value_json, formula FROM sum_cells WHERE sheet_id = ? AND row_index = ? AND column_index = ?",
                        (str(sheet_id), row_index, column_index),
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
                        "INSERT INTO sum_cells (sheet_id, row_index, column_index, value_type, value_json, formula, style_json, revision, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sheet_id, row_index, column_index) DO UPDATE SET value_type = excluded.value_type, value_json = excluded.value_json, formula = excluded.formula, style_json = excluded.style_json, revision = excluded.revision, updated_at = excluded.updated_at",
                        (
                            str(sheet_id),
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
                    continue
                connection.execute(
                    "INSERT INTO sum_cells (sheet_id, row_index, column_index, value_type, value_json, formula, style_json, revision, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sheet_id, row_index, column_index) DO UPDATE SET value_type = excluded.value_type, value_json = excluded.value_json, formula = excluded.formula, style_json = excluded.style_json, revision = excluded.revision, updated_at = excluded.updated_at",
                    (
                        str(sheet_id),
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
            self._ensure_axis_metadata(connection, str(sheet_id), normalized)
            connection.execute(
                "INSERT INTO sum_operations (operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
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

        new_path, size, checksum = self._save_file(workbook_id, revision, updated)
        cursor = self._db.execute(
            "UPDATE sum_workbooks SET current_revision = ?, blob_path = ?, file_size = ?, checksum = ?, updated_at = ? WHERE id = ? AND current_revision = ?",
            (revision, new_path, size, checksum, now, workbook_id, current_revision),
        )
        try:
            affected = int(cursor.rowcount)
        finally:
            cursor.close()
        if affected != 1:
            self._blob.delete(new_path)
            raise SumConflictError("Workbook revision changed during operation")
        self._cache_current_file(workbook_id, revision, updated)
        self._prune_revision_blobs(workbook_id)
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

        Cells are moved deterministically (read, delete, re-insert) so SQLite
        primary-key ordering can never collide regardless of processing order.
        """
        if axis == "row":
            index_field = "row_index"
            axis_table = "sum_rows"
            column_key_field = None
        else:
            index_field = "column_index"
            axis_table = "sum_columns"
            column_key_field = "column_key"

        # 1. Remove the deleted band first (delete ops only).
        if not insert:
            connection.execute(
                f"DELETE FROM sum_cells WHERE sheet_id = ? AND {index_field} >= ? AND {index_field} < ?",
                (sheet_id, at, at + count),
            )

        # 2. Move the surviving cells that shift.
        if insert:
            source_bound = at
            delta = count
        else:
            source_bound = at + count
            delta = -count
        affected = connection.execute(
            f"SELECT {index_field} AS idx, column_index, row_index, value_type, value_json, formula, style_json, revision, updated_at FROM sum_cells WHERE sheet_id = ? AND {index_field} >= ?",
            (sheet_id, source_bound),
        ).fetchall()
        if len(affected) > MAX_STRUCTURAL_CELLS:
            raise SumValidationError(
                "Operation affects too many cells; split it into smaller changes"
            )
        if affected:
            shifted: List[Tuple[Any, ...]] = []
            for row in affected:
                if axis == "row":
                    new_index = int(row["row_index"]) + delta
                    column_index = int(row["column_index"])
                else:
                    new_index = int(row["column_index"]) + delta
                    column_index = new_index
                shifted.append(
                    (
                        sheet_id,
                        new_index if axis == "row" else int(row["row_index"]),
                        new_index if axis == "column" else column_index,
                        row["value_type"],
                        row["value_json"],
                        row["formula"],
                        row["style_json"],
                        int(row["revision"]),
                        int(row["updated_at"]),
                    )
                )
            connection.execute(
                f"DELETE FROM sum_cells WHERE sheet_id = ? AND {index_field} >= ?",
                (sheet_id, source_bound),
            )
            connection.executemany(
                "INSERT INTO sum_cells (sheet_id, row_index, column_index, value_type, value_json, formula, style_json, revision, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                shifted,
            )

        # 3. Axis metadata (sum_rows / sum_columns) stays in sync.
        if insert:
            connection.execute(
                f"UPDATE {axis_table} SET position = position + ? WHERE sheet_id = ? AND position >= ?",
                (count, sheet_id, at),
            )
            now = int(time.time() * 1000)
            for i in range(at, at + count):
                connection.execute(
                    f"INSERT OR IGNORE INTO {axis_table} (id, sheet_id, stable_key, position, created_at) VALUES (?, ?, ?, ?, ?)",
                    (str(self._generate_id()), sheet_id, f"{axis}-{i}", i, now),
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

        # 4. Query statistics and managed indexes follow column moves.
        if column_key_field:
            if insert:
                connection.execute(
                    "UPDATE sum_statistics SET column_key = CAST(CAST(column_key AS INTEGER) + ? AS TEXT) WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
                    (count, sheet_id, at),
                )
            else:
                connection.execute(
                    "DELETE FROM sum_statistics WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ? AND CAST(column_key AS INTEGER) < ?",
                    (sheet_id, at, at + count),
                )
                connection.execute(
                    "UPDATE sum_statistics SET column_key = CAST(CAST(column_key AS INTEGER) - ? AS TEXT) WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
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
        except sqlite3.Error:
            pass

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
        rows_needed: Dict[int, None] = {}
        columns_needed: Dict[int, None] = {}
        for cell in cells:
            rows_needed.setdefault(int(cell["row"]), None)
            columns_needed.setdefault(int(cell["column"]), None)
        for row_index in rows_needed:
            connection.execute(
                "INSERT OR IGNORE INTO sum_rows (id, sheet_id, stable_key, position, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(self._generate_id()),
                    sheet_id,
                    f"row-{row_index}",
                    row_index,
                    now,
                ),
            )
        for column_index in columns_needed:
            connection.execute(
                "INSERT OR IGNORE INTO sum_columns (id, sheet_id, stable_key, display_name, data_type, position, created_at) VALUES (?, ?, ?, ?, 'text', ?, ?)",
                (
                    str(self._generate_id()),
                    sheet_id,
                    f"col-{column_index}",
                    _column_letter(column_index),
                    column_index,
                    now,
                ),
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
        crossed: List[str] = []
        for column_index, cell_count in counts.items():
            column_key = str(column_index)
            previous_row = connection.execute(
                "SELECT access_count FROM sum_statistics WHERE sheet_id = ? AND column_key = ?",
                (sheet_id, column_key),
            ).fetchone()
            previous = int(previous_row["access_count"]) if previous_row else 0
            connection.execute(
                "INSERT INTO sum_statistics (sheet_id, column_key, access_count, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(sheet_id, column_key) DO UPDATE SET access_count = sum_statistics.access_count + excluded.access_count, updated_at = excluded.updated_at",
                (sheet_id, column_key, cell_count, now),
            )
            if previous < INDEX_PROMOTION_THRESHOLD <= previous + cell_count:
                crossed.append(column_key)
        if crossed:
            try:
                self._promote_indexes(connection, sheet_id, now, crossed)
            except sqlite3.Error:
                pass
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
        except sqlite3.Error:
            pass

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
                "SELECT value_json, row_index FROM sum_cells WHERE sheet_id = ? AND column_index = ? AND value_type != 'blank'",
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
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            rows = connection.execute(
                "SELECT operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at FROM sum_operations WHERE result_revision > ? ORDER BY result_revision ASC LIMIT ?",
                (int(after_revision), limit),
            ).fetchall()
        finally:
            self._close_sqlite(connection, path)
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
        data = self._file_bytes(workbook_id)
        snapshot_id = self._generate_id()
        key = f"{int(workbook_id)}/snapshot-{snapshot_id}.plexisum"
        path = self._blob.store(data, key)
        checksum = hashlib.sha256(data).hexdigest()
        now = int(time.time() * 1000)
        connection, tmp_path = self._open_sqlite(data)
        try:
            cell_count = int(
                connection.execute("SELECT COUNT(*) AS c FROM sum_cells").fetchone()[
                    "c"
                ]
            )
        except sqlite3.Error:
            cell_count = 0
        finally:
            self._close_sqlite(connection, tmp_path)
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
                if snap.get("blob_path"):
                    try:
                        self._blob.delete(str(snap["blob_path"]))
                    except Exception:
                        pass
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
            data = self._blob.retrieve(str(row["blob_path"]))
        except Exception as exc:
            raise SumStorageError("Snapshot file could not be read") from exc
        if row.get("checksum"):
            actual = hashlib.sha256(data).hexdigest()
            if actual != row["checksum"]:
                raise SumValidationError("Snapshot checksum mismatch")
        current = self._db.fetch_one(
            "SELECT current_revision FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        base_revision = int(current["current_revision"]) if current else 0
        revision = base_revision + 1
        now = int(time.time() * 1000)
        path, size, checksum = self._save_file(workbook_id, revision, data)
        cursor = self._db.execute(
            "UPDATE sum_workbooks SET current_revision = ?, blob_path = ?, file_size = ?, checksum = ?, updated_at = ? WHERE id = ? AND current_revision = ?",
            (revision, path, size, checksum, now, workbook_id, base_revision),
        )
        try:
            affected = int(cursor.rowcount)
        finally:
            cursor.close()
        if affected != 1:
            self._blob.delete(path)
            raise SumConflictError("Workbook revision changed during restore")
        self._cache_current_file(workbook_id, revision, data)
        self._prune_revision_blobs(workbook_id)
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
        path, size, checksum = self._save_file(workbook_id, revision, updated)
        cursor = self._db.execute(
            "UPDATE sum_workbooks SET current_revision = ?, blob_path = ?, file_size = ?, checksum = ?, updated_at = ? WHERE id = ? AND current_revision = ?",
            (revision, path, size, checksum, now, workbook_id, base),
        )
        try:
            affected = int(cursor.rowcount)
        finally:
            cursor.close()
        if affected != 1:
            self._blob.delete(path)
            raise SumConflictError("Workbook revision changed during update")
        self._cache_current_file(workbook_id, revision, updated)
        self._prune_revision_blobs(workbook_id)
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
        base_revision: int,
    ) -> Dict[str, Any]:
        import csv as csv_module
        import io as io_module

        self._workbook(user_id, workbook_id, mutation=True)
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

        new_path, size, checksum = self._save_file(workbook_id, revision, updated)
        cursor = self._db.execute(
            "UPDATE sum_workbooks SET current_revision = ?, blob_path = ?, file_size = ?, checksum = ?, updated_at = ? WHERE id = ? AND current_revision = ?",
            (revision, new_path, size, checksum, now, workbook_id, current_revision),
        )
        try:
            affected = int(cursor.rowcount)
        finally:
            cursor.close()
        if affected != 1:
            self._blob.delete(new_path)
            raise SumConflictError("Workbook revision changed during import")
        self._cache_current_file(workbook_id, revision, updated)
        self._prune_revision_blobs(workbook_id)
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
        for cell in cells:
            if cell.get("value_type") in ("blank", "__preserve__", "formula"):
                continue
            row_index = int(cell["row"])
            column_index = int(cell["column"])
            for rule in rules:
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
                break

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
            except Exception:
                pass
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
        uid = int(user_id)
        workbooks = self._db.fetch_all(
            "SELECT id, blob_path, current_revision FROM sum_workbooks WHERE owner_id = ?",
            (uid,),
        )
        workbook_ids = [int(wb["id"]) for wb in workbooks]

        # Blob erasure is best-effort; the storage adapter may not expose a
        # listing API, so revision blobs are removed by their deterministic
        # ``rev-N`` key names up to the current revision. The directory is
        # derived from the stored current blob path so the same code works
        # for the media adapter (``plexisum/``-namespaced) and the local
        # filesystem fallback alike.
        for wb in workbooks:
            wb_id = int(wb["id"])
            _WORKBOOK_FILE_CACHE.drop_workbook(wb_id)
            try:
                current = int(wb.get("current_revision") or 0)
            except (TypeError, ValueError):
                current = 0
            current_path = wb.get("blob_path")
            directory = (
                str(current_path).rsplit("/", 1)[0] if current_path else f"{wb_id}"
            )
            for revision in range(current + 1):
                try:
                    self._blob.delete(f"{directory}/rev-{revision}.plexisum")
                except Exception:
                    pass
            # Snapshot file blobs (path is stored verbatim in the row).
            snapshots = self._db.fetch_all(
                "SELECT blob_path FROM sum_snapshots WHERE workbook_id = ?",
                (wb_id,),
            )
            for snap in snapshots:
                snap_path = snap.get("blob_path")
                if snap_path:
                    try:
                        self._blob.delete(str(snap_path))
                    except Exception:
                        pass

        # Metadata (children before parents for FK-enabled PostgreSQL).
        try:
            self._db.begin_transaction()
            for wb_id in workbook_ids:
                self._db.execute(
                    "DELETE FROM sum_range_permissions WHERE workbook_id = ?",
                    (wb_id,),
                )
                self._db.execute(
                    "DELETE FROM sum_grants WHERE workbook_id = ?", (wb_id,)
                )
                self._db.execute(
                    "DELETE FROM sum_snapshots WHERE workbook_id = ?", (wb_id,)
                )
                self._db.execute("DELETE FROM sum_workbooks WHERE id = ?", (wb_id,))
            # Grants granted *to* the user on other people's workbooks must
            # also be revoked for full erasure.
            self._db.execute("DELETE FROM sum_grants WHERE subject_id = ?", (uid,))
            self._db.commit()
        except Exception:
            try:
                self._db.rollback()
            except Exception:
                pass
            raise

        try:
            # Workbook ids are captured before deletion so the audit rows they
            # referenced are removed too.
            if workbook_ids:
                placeholders = ",".join("?" * len(workbook_ids))
                self._db.execute(
                    f"DELETE FROM suite_audit_log WHERE actor_id = ? OR resource_id IN ({placeholders})",
                    (uid, *workbook_ids),
                )
            else:
                self._db.execute(
                    "DELETE FROM suite_audit_log WHERE actor_id = ?", (uid,)
                )
        except Exception:
            # suite_audit_log lives in the Stash schema; tolerate isolated
            # Sum-only databases where it has not been created.
            pass
        try:
            import utils.logger as logger

            logger.debug("Purged PlexiSum data for user %s", uid)
        except Exception:
            pass
