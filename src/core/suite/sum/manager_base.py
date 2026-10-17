"""Sum manager mixin: construction, audit, authorization, presence (split from manager.py)."""

import json
import os
import re
import time

import utils.config as config
from typing import Any, Dict, List, Optional

from .manager_protocol import SumManagerProtocol
from ._shared import (
    LocalBlobStore,
    MediaBlobStore,
    SumNotFoundError,
    SumPermissionError,
    SumStorageError,
    SumValidationError,
    _PRESENCE_LOCK,
    _WORKBOOK_PRESENCE,
    _prune_presence,
)


class SumManagerBase(SumManagerProtocol):
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
        """Resolve @usernames in a comment body to user ids.

        Returns the mentioned ids so the WORKBOOK_UPDATE event can carry them
        for in-workspace toasts. That realtime carry is the delivery channel:
        ``notif_notifications`` requires a backing chat message
        (``message_id``/``conversation_id`` are NOT NULL foreign keys to the
        msg tables, and ``UNIQUE(user_id, message_id)`` collides on any
        message-less placeholder), which a workbook comment has no way to
        satisfy -- so no bell row is written here. Best-effort and
        non-raising.
        """
        mentioned_ids: List[int] = []
        try:
            usernames = self._extract_mentions(comment.get("body"))
            if not usernames:
                return mentioned_ids
            placeholders = ",".join("?" for _ in usernames)
            rows = self._db.fetch_all(
                f"SELECT id FROM auth_users WHERE LOWER(username) IN ({placeholders}) "
                "AND deletion_status = 'active'",
                [name.lower() for name in usernames],
            )
            author_id = int(comment.get("author_id") or 0)

            for row in rows:
                target_id = int(row["id"])
                if target_id == author_id:
                    continue
                mentioned_ids.append(target_id)
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
