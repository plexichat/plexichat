"""Sum manager mixin: workbook lifecycle and upload validation (split from manager.py)."""

import base64
import os
import sqlite3
import tempfile
import time

from typing import Any, Dict, List, Optional, Tuple

from .schema import (
    PLEXISUM_FORMAT_VERSION,
)
from .manager_protocol import SumManagerProtocol
from ._shared import (
    SumConflictError,
    SumNotFoundError,
    SumPermissionError,
    SumStorageError,
    SumValidationError,
)


class SumManagerWorkbooks(SumManagerProtocol):
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
