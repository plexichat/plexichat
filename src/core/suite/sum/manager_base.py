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
                # SECURITY: only a genuine integer id from the authoritative
                # sum_grants.subject_id row may attest a grant subject. int()
                # laundering of a malformed value ("07"->7, 7.9->7, " 8 "->8,
                # True->1) would admit the wrong principal to the authorized
                # viewer audience (WORKBOOK_UPDATE realtime fan-out) even
                # though no valid grant was ever made to them. type() also
                # rejects bool. Legitimate genuine-int grants are preserved.
                subject_id = row["subject_id"]
                if type(subject_id) is int:
                    ids[subject_id] = None
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
                    # SECURITY: only a genuine integer id from the authoritative
                    # srv_members roster may attest a viewer of a server-scoped
                    # workbook. get_member_user_ids() returns the raw
                    # srv_members.user_id projection with no type filtering, so
                    # int() laundering of a malformed-but-coercible value
                    # ("07"->7, " 8 "->8, 7.9->7, True->1) would admit the wrong
                    # principal to the authorized-viewer audience (WORKBOOK_UPDATE
                    # realtime fan-out) even though no genuine membership for
                    # that id was ever proven. type() also rejects bool.
                    # Legitimate genuine-int members are preserved.
                    if type(member_id) is int:
                        ids[member_id] = None
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

    @staticmethod
    def _decode_grant_cell(value: Optional[str]) -> set:
        """Decode one ``sum_grants.permissions`` cell.

        Kept as the single decoder for both the per-workbook and the batched
        grant lookups so their permission semantics cannot drift.
        """
        perms = SumManagerBase._decode(value, [])
        if isinstance(perms, list):
            return {p for p in perms if isinstance(p, str)}
        if isinstance(perms, str):
            return set(perms.split(","))
        return set()

    def _grant_permissions(self, workbook_id: int, user_id: int) -> frozenset:
        """All live grant permissions for ``user_id`` on ``workbook_id``.

        The authorization and viewer-permission paths used to probe the same
        grant rows two or three times per request (``edit``, then ``view``,
        then ``edit`` again), each with a fresh ``now`` so the database query
        cache could not collapse them. Resolve the rows once and decode every
        permission string with the same list/comma handling ``_has_grant``
        used, so permission semantics stay identical while the hot path issues
        a single grant query per check.
        """
        now = int(time.time() * 1000)
        rows = self._db.fetch_all(
            "SELECT permissions FROM sum_grants WHERE workbook_id = ? AND subject_id = ? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
            (int(workbook_id), int(user_id), now),
        )
        permissions: set = set()
        for row in rows:
            permissions.update(self._decode_grant_cell(row.get("permissions")))
        return frozenset(permissions)

    def _grant_permissions_bulk(
        self, workbook_ids: List[int], user_id: int, now: Optional[int] = None
    ) -> Dict[int, frozenset]:
        """Live grant permissions for one viewer across many workbooks.

        Used by workbook listing, which needs the same ``my_permissions``
        projection as ``get_workbook`` for every row on the page. Resolving
        each row separately made a page of N shared workbooks issue N grant
        queries; this issues one query keyed to BOTH the requesting viewer and
        the page's workbook ids. Grant rows for other subjects, revoked rows,
        and expired rows are filtered in SQL with the same predicates as
        ``_grant_permissions``, so the result is fail-closed and cannot leak a
        third party's grants. Callers may pass the listing's own ``now`` so the
        permission projection is consistent with the visibility filter.
        """
        ids = [int(workbook_id) for workbook_id in workbook_ids]
        if not ids:
            return {}
        if now is None:
            now = int(time.time() * 1000)
        placeholders = ",".join("?" for _ in ids)
        rows = self._db.fetch_all(
            "SELECT workbook_id, permissions FROM sum_grants "
            "WHERE subject_id = ? AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?) "
            f"AND workbook_id IN ({placeholders})",
            (int(user_id), int(now), *ids),
        )
        by_workbook: Dict[int, set] = {}
        for row in rows:
            workbook_id = int(row["workbook_id"])
            by_workbook.setdefault(workbook_id, set()).update(
                self._decode_grant_cell(row.get("permissions"))
            )
        return {
            workbook_id: frozenset(permissions)
            for workbook_id, permissions in by_workbook.items()
        }

    def _has_grant(
        self, workbook_id: int, user_id: int, permission: str = "view"
    ) -> bool:
        return permission in self._grant_permissions(workbook_id, user_id)

    def _require_workbook_owner(self, workbook: Dict[str, Any], user_id: int) -> None:
        """Require owner-level authority for destructive/lifecycle changes.

        ``_authorize(..., mutation=True)`` deliberately lets any ``edit``
        grantee mutate cells, but lifecycle transitions (delete/restore) and
        ACL changes are more powerful than cell editing: a workbook only has
        ``view``/``edit`` grants, so an editor must never be able to delete or
        resurrect a workbook they do not own.  Only the owner -- or a server
        manager for a server-scoped workbook -- may perform these acts, and a
        missing/failing server module fails closed.
        """
        uid = int(user_id)
        if int(workbook["owner_id"]) == uid:
            return
        server_id = workbook.get("server_id")
        if workbook.get("scope") == "server" and server_id is not None:
            try:
                if self._servers and self._servers.has_permission(
                    uid, int(server_id), "server.manage"
                ):
                    return
            except Exception:
                pass
        raise SumPermissionError("Not authorized to manage this workbook")

    def _authorize(
        self, workbook: Dict[str, Any], user_id: int, mutation: bool = False
    ) -> None:
        uid = int(user_id)
        if int(workbook["owner_id"]) == uid:
            return
        wb_id = int(workbook["id"])
        grants = self._grant_permissions(wb_id, uid)
        if "edit" in grants:
            return
        if not mutation and "view" in grants:
            return
        server_id = workbook.get("server_id")
        if workbook["scope"] == "server" and server_id is not None:
            if self._is_member(uid, int(server_id)):
                if not mutation:
                    return
                try:
                    if self._servers and self._servers.has_permission(
                        uid, int(server_id), "server.manage"
                    ):
                        return
                except Exception as permission_error:
                    import utils.logger as logger

                    logger.debug(
                        f"sum workbook permission probe failed: {permission_error}"
                    )
        if mutation and "view" in grants:
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
        grants: Optional[frozenset] = None,
    ) -> Dict[str, Any]:
        """Effective access for ``user_id`` on a workbook.

        Returns ``can_view``/``can_edit`` plus the per-sheet ``read_only``
        ranges that apply to this user so clients can render permission-aware
        editing without trusting client-supplied capability labels. The owner
        bypasses range protections; every other authorized user (including
        edit grantees) is blocked inside ranges the owner locked read-only.

        ``grants`` may carry a precomputed grant set for this same viewer and
        workbook (used by the batched listing path). When omitted, it is
        resolved locally with one grant query.
        """
        if workbook is None:
            workbook = self._workbook(user_id, workbook_id)
        wb_id = int(workbook["id"])
        uid = int(user_id)
        is_owner = int(workbook["owner_id"]) == uid
        # One grant read per check replaces the up-to-three ``_has_grant``
        # probes this method used to make (edit, view, then edit again).
        # Batched callers (workbook listing) pass the viewer's grants for this
        # workbook so a whole page costs one query instead of one per row.
        # The owner always bypasses the grant table regardless of what a caller
        # passes, so this remains fail-closed for direct callers too.
        if is_owner:
            grants = frozenset()
        elif grants is None:
            grants = self._grant_permissions(wb_id, uid)

        can_edit = is_owner or "edit" in grants
        server_id = workbook.get("server_id")
        member: Optional[bool] = None

        def is_server_member() -> bool:
            nonlocal member
            if member is None:
                member = (
                    workbook.get("scope") == "server"
                    and server_id is not None
                    and self._is_member(uid, int(server_id))
                )
            return bool(member)

        if not can_edit and is_server_member():
            try:
                if self._servers and self._servers.has_permission(
                    uid, int(server_id) if server_id is not None else 0, "server.manage"
                ):
                    can_edit = True
            except Exception as grant_error:
                import utils.logger as logger

                logger.debug(f"sum edit grant probe failed: {grant_error}")

        reason = None
        if not can_edit:
            has_view = "view" in grants or "edit" in grants
            if not has_view:
                has_view = is_server_member()
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
