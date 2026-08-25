"""Sum manager mixin: grants, features, range permissions, purge (split from manager.py)."""

import time

from typing import Any, Dict, List, Optional

from .manager_protocol import SumManagerProtocol
from ._shared import (
    MAX_PURGE_BACKUP_BYTES,
    SumNotFoundError,
    SumStorageError,
    SumValidationError,
    _WORKBOOK_FILE_CACHE,
)


class SumManagerAdmin(SumManagerProtocol):
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
