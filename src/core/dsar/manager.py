import os
import time
from typing import List, Optional, Dict, Any

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id


# Hard upper bound for the admin listing endpoint. Mirrors the ``le=200``
# taken by the sibling admin routes (see src/api/routes/admin/audit.py) and
# caps direct core callers that bypass the route.
MAX_ADMIN_PAGE_SIZE = 200


class DSARManager:
    """
    High-level API for DSAR operations.
    Manages data export requests, approvals, and downloads.
    """

    def __init__(self, db):
        self._db = db
        self._audit_log = None
        self._harvester = None

    def _get_audit_log(self):
        """Lazy load audit log to avoid circular imports."""
        if self._audit_log is None:
            from .audit_log import DSARLog

            self._audit_log = DSARLog()
        return self._audit_log

    def _get_harvester(self):
        """Lazy load harvester to avoid circular imports."""
        if self._harvester is None:
            from .harvester import DSARHarvester

            self._harvester = DSARHarvester(self._db)
        return self._harvester

    def _invalidate_cache(self, pattern: str):
        """Invalidate cache for DSAR requests."""
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern(pattern)
        except Exception:
            pass

    def request_export(
        self, user_id: int, format: str = "json", categories: Optional[List[str]] = None
    ):
        """
        Create a new DSAR export request.
        """
        allowed_formats = {"json", "zip"}
        if format not in allowed_formats:
            raise ValueError(
                f"Unsupported export format: {format!r}. "
                f"Allowed formats: {sorted(allowed_formats)}"
            )

        request_id = generate_snowflake_id()
        now = int(time.time())

        metadata = {"categories": categories} if categories else None
        import json as _json

        metadata_json = _json.dumps(metadata) if metadata else None

        self._db.execute(
            """
            INSERT INTO dsar_requests
            (id, user_id, status, requested_at, format, metadata)
            VALUES (?, ?, 'pending', ?, ?, ?)
            """,
            (request_id, user_id, now, format, metadata_json),
        )

        self._invalidate_cache("dsar_request:*")

        audit_log = self._get_audit_log()
        audit_log.log_event(
            user_id,
            "REQUESTED",
            f"user:{user_id}",
            {"request_id": request_id, "format": format},
        )

        logger.info(f"DSAR: Created export request {request_id} for user {user_id}")

        return self._get_request_by_id(request_id)

    def approve_request(self, request_id: int, admin_id: int):
        """
        Admin approves a DSAR request.
        """
        request = self._db.fetch_one(
            "SELECT * FROM dsar_requests WHERE id = ?", (request_id,)
        )
        if not request:
            raise ValueError(f"DSAR request {request_id} not found")

        if request["status"] not in ("pending",):
            raise ValueError(f"Cannot approve request in status: {request['status']}")

        self._db.execute(
            """
            UPDATE dsar_requests
            SET status = 'approved', admin_id = ?
            WHERE id = ?
            """,
            (admin_id, request_id),
        )

        self._invalidate_cache("dsar_request:*")

        audit_log = self._get_audit_log()
        audit_log.log_event(
            request["user_id"],
            "APPROVED",
            f"user:{request['user_id']}",
            {"request_id": request_id, "admin_id": admin_id},
        )

        logger.info(f"DSAR: Admin {admin_id} approved request {request_id}")

        return self._get_request_by_id(request_id)

    def deny_request(self, request_id: int, admin_id: int, reason: str):
        """
        Admin denies a DSAR request.
        """
        request = self._db.fetch_one(
            "SELECT * FROM dsar_requests WHERE id = ?", (request_id,)
        )
        if not request:
            raise ValueError(f"DSAR request {request_id} not found")

        if request["status"] != "pending":
            raise ValueError(f"Cannot deny request in status: {request['status']}")

        self._db.execute(
            """
            UPDATE dsar_requests
            SET status = 'denied', admin_id = ?, denial_reason = ?
            WHERE id = ?
            """,
            (admin_id, reason, request_id),
        )

        self._invalidate_cache("dsar_request:*")

        audit_log = self._get_audit_log()
        audit_log.log_event(
            request["user_id"],
            "DENIED",
            f"user:{request['user_id']}",
            {"request_id": request_id, "admin_id": admin_id, "reason": reason},
        )

        logger.info(f"DSAR: Admin {admin_id} denied request {request_id}: {reason}")

        return self._get_request_by_id(request_id)

    def cancel_request(self, request_id: int, user_id: int):
        """
        User cancels their own DSAR request.
        """
        request = self._db.fetch_one(
            "SELECT * FROM dsar_requests WHERE id = ?", (request_id,)
        )
        if not request:
            raise ValueError(f"DSAR request {request_id} not found")

        if request["user_id"] != user_id:
            raise PermissionError("Cannot cancel another user's request")

        if request["status"] in ("ready", "downloaded", "expired", "failed"):
            raise ValueError(f"Cannot cancel request in status: {request['status']}")

        # SECURITY: if the request had already completed and a
        # generated export file exists, cancel MUST scrub the file
        # from storage. Previously the row was merely marked
        # ``cancelled`` while the export stayed on disk forever —
        # users have a GDPR right to deletion and an undeleted file
        # is an active compliance breach.
        self._scrub_export_files(request)

        self._db.execute(
            "UPDATE dsar_requests SET status = 'cancelled' WHERE id = ?",
            (request_id,),
        )

        self._invalidate_cache("dsar_request:*")

        audit_log = self._get_audit_log()
        audit_log.log_event(
            user_id,
            "CANCELLED",
            f"user:{user_id}",
            {"request_id": request_id},
        )

        logger.info(f"DSAR: User {user_id} cancelled request {request_id}")

        return self._get_request_by_id(request_id)

    def get_user_requests(self, user_id: int) -> List[Dict[str, Any]]:
        """List all DSAR requests for a user."""
        requests = self._db.fetch_all(
            """
            SELECT * FROM dsar_requests
            WHERE user_id = ?
            ORDER BY requested_at DESC
            """,
            (user_id,),
        )
        return [dict(r) for r in requests]

    def get_request_status(
        self, request_id: int, user_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific DSAR request status (user can only see their own)."""
        request = self._db.fetch_one(
            "SELECT * FROM dsar_requests WHERE id = ? AND user_id = ?",
            (request_id, user_id),
        )
        return dict(request) if request else None

    def get_export_file(
        self, request_id: int, user_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Get storage path, backend, and checksum for download.
        User can only access their own requests.
        """
        request = self._db.fetch_one(
            "SELECT * FROM dsar_requests WHERE id = ? AND user_id = ?",
            (request_id, user_id),
        )
        if not request:
            return None

        if request["status"] not in ("ready", "downloaded"):
            return None

        now = int(time.time())
        if request["expires_at"] and request["expires_at"] < now:
            # SECURITY: the previous implementation merely flipped
            # the row to ``expired`` but did not delete the file.
            # Expired DSAR exports accumulate forever on disk and
            # are a compliance time-bomb. We must scrub the file
            # atomically with the status flip.
            self._scrub_export_files(request)
            self._db.execute(
                "UPDATE dsar_requests SET status = 'expired' WHERE id = ?",
                (request_id,),
            )
            return None

        # Deliberately do NOT flip the status to 'downloaded' here. The
        # status is advanced only after the bytes have actually been
        # streamed to the client (see the API route), so a failed or
        # partial download leaves the export 'ready' and re-downloadable
        # instead of becoming a permanently unrecoverable 400.
        if not request.get("storage_path"):
            return None
        backend = request.get("storage_backend", "local")
        # Local-path existence can only be checked for the local
        # backend; database/S3 backends store objects keyed by a
        # relative path that will never exist on this filesystem
        # (checking it broke downloads for DB-blob deployments).
        if backend == "local" and not os.path.exists(request["storage_path"]):
            # The file is gone (e.g. cleaned up) even though the row still
            # says it is available; report unavailable so the client can
            # request a fresh export. Never raise here.
            return None

        self._invalidate_cache("dsar_request:*")

        return {
            "storage_backend": request.get("storage_backend", "local"),
            "storage_path": request.get("storage_path"),
            "checksum": request["checksum"],
            "file_size": request["file_size_bytes"],
            "format": request["format"],
            "status": request["status"],
        }

    def mark_export_downloaded(self, request_id: int, user_id: int) -> None:
        """Mark an export as downloaded after the bytes have been streamed.

        Called by the API route after a successful download so the
        one-shot semantics hold without ever blocking a failed download.
        Idempotent: only flips a 'ready' row, leaving 'downloaded' rows
        untouched.
        """
        self._db.execute(
            "UPDATE dsar_requests SET status = 'downloaded' WHERE id = ? "
            "AND user_id = ? AND status = 'ready'",
            (request_id, user_id),
        )
        self._invalidate_cache("dsar_request:*")

        audit_log = self._get_audit_log()
        audit_log.log_event(
            user_id,
            "DOWNLOADED",
            f"user:{user_id}",
            {"request_id": request_id},
        )

    def get_admin_requests(
        self, status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Admin lists all DSAR requests with optional status filter.

        ``limit``/``offset`` are clamped before they reach SQL. SQLite reads
        ``LIMIT -1`` as *unlimited*, so an unclamped negative limit dumped
        every DSAR row (full export history for every user) regardless of the
        requested page; a negative offset was likewise passed through
        verbatim.
        """
        limit = max(0, min(int(limit), MAX_ADMIN_PAGE_SIZE))
        offset = max(0, int(offset))
        if status:
            requests = self._db.fetch_all(
                """
                SELECT * FROM dsar_requests
                WHERE status = ?
                ORDER BY requested_at DESC
                LIMIT ? OFFSET ?
                """,
                (status, limit, offset),
            )
        else:
            requests = self._db.fetch_all(
                """
                SELECT * FROM dsar_requests
                ORDER BY requested_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
        return [dict(r) for r in requests]

    def generate_manual(self, request_id: int, admin_id: int):
        """
        Admin manually triggers generation for a request.
        """
        request = self._db.fetch_one(
            "SELECT * FROM dsar_requests WHERE id = ?", (request_id,)
        )
        if not request:
            raise ValueError(f"DSAR request {request_id} not found")

        if request["status"] not in ("pending", "approved"):
            raise ValueError(
                f"Cannot generate for request in status: {request['status']}"
            )

        # SECURITY (M3): transitions are guarded so a concurrent cancel
        # cannot be overwritten. If the row is no longer pending/approved
        # the request was cancelled or denied - refuse to resurrect it.
        cursor = self._db.execute(
            "UPDATE dsar_requests SET status = 'approved', admin_id = ? "
            "WHERE id = ? AND status IN ('pending', 'approved')",
            (admin_id, request_id),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            raise ValueError(
                f"Request {request_id} changed state concurrently; refusing to generate"
            )

        from .collector import DataCollector
        from .export_formats import ExportFormatGenerator

        user_id = request["user_id"]
        export_format = request.get("format", "json")

        cursor = self._db.execute(
            "UPDATE dsar_requests SET status = 'generating' "
            "WHERE id = ? AND status = 'approved'",
            (request_id,),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            raise ValueError(
                f"Request {request_id} was cancelled before generation started"
            )

        try:
            collector = DataCollector(self._db)
            data = collector.collect_all(user_id)

            generator = ExportFormatGenerator(db=self._db)

            retention_days = (
                config.get("dsar", {}).get("harvester", {}).get("retention_days", 7)
            )
            expires_at = int(time.time()) + (retention_days * 86400)

            if export_format == "zip":
                storage_path, checksum, file_size = generator.generate_zip(
                    data, request_id, user_id
                )
            else:
                storage_path, checksum, file_size = generator.generate_json(
                    data, request_id, user_id
                )

            # SECURITY (M3): conditional completion update. If the user
            # cancelled while generation ran (rowcount == 0) delete the
            # artifact instead of resurrecting a revoked export.
            cursor = self._db.execute(
                """
                UPDATE dsar_requests
                SET status = 'ready',
                    completed_at = ?,
                    expires_at = ?,
                    storage_backend = ?,
                    storage_path = ?,
                    checksum = ?,
                    file_size_bytes = ?
                WHERE id = ? AND status = 'generating'
                """,
                (
                    int(time.time()),
                    expires_at,
                    generator.backend_name,
                    storage_path,
                    checksum,
                    file_size,
                    request_id,
                ),
            )
            if getattr(cursor, "rowcount", 0) == 0:
                logger.warning(
                    f"DSAR: request {request_id} cancelled during manual "
                    f"generation; deleting generated artifact"
                )
                generator.delete(storage_path)
                raise ValueError(
                    f"Request {request_id} was cancelled during generation"
                )

            audit_log = self._get_audit_log()
            audit_log.log_event(
                user_id,
                "READY",
                f"user:{user_id}",
                {
                    "request_id": request_id,
                    "admin_id": admin_id,
                    "file_size": file_size,
                },
            )

            logger.info(
                f"DSAR: Admin {admin_id} manually generated request {request_id}"
            )

            return self._get_request_by_id(request_id)

        except Exception as e:
            self._db.execute(
                """
                UPDATE dsar_requests
                SET status = 'failed', error_message = ?
                WHERE id = ? AND status = 'generating'
                """,
                (str(e), request_id),
            )
            raise

    def _get_request_by_id(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Get a request by ID."""
        request = self._db.fetch_one(
            "SELECT * FROM dsar_requests WHERE id = ?", (request_id,)
        )
        return dict(request) if request else None

    def _scrub_export_files(self, request: Dict[str, Any]) -> None:
        """Securely delete any generated export files for a request.

        Called from cancel, expiry, and any other path that
        transitions a request to a state where its export must no
        longer exist on disk. We delete through the storage backend
        that wrote the export (local, S3/RustFS, or database) and log
        the outcome.

        Deletion MUST go through the backend rather than straight at
        the object it stored: ``dsar_requests.storage_path`` is the
        backend-relative path, and S3/RustFS prepend their configured
        ``path_prefix`` (the local backend its base directory) when
        they write the object. Deleting the bare ``storage_path`` off
        the S3 client missed the real key and left the export - full
        PII - sitting in the bucket past its retention window.
        """
        storage_backend = request.get("storage_backend")
        storage_path = request.get("storage_path")
        if not storage_path:
            return

        # Backend-aware delete: ``ExportFormatGenerator`` builds the
        # same backend (and therefore the same key layout) used to
        # write the export. It reports failure instead of raising, but
        # a construction failure (bad S3 credentials, boto3 missing)
        # is still possible, so keep the local fallback below
        # reachable regardless.
        try:
            from .export_formats import ExportFormatGenerator

            if ExportFormatGenerator(db=self._db).delete(str(storage_path)):
                logger.info(
                    f"DSAR: scrubbed export object for request "
                    f"{request.get('id')} ({storage_backend}) at {storage_path}"
                )
            else:
                logger.warning(
                    f"DSAR: export object for request {request.get('id')} "
                    f"({storage_backend}) at {storage_path} was not deleted; "
                    "it may still exist in the storage backend"
                )
        except Exception as e:
            logger.warning(
                f"DSAR: failed to scrub export object for request "
                f"{request.get('id')}: {e}"
            )

        # Local fallback for rows holding an absolute filesystem path
        # (legacy rows written before export paths were backend-relative).
        # The backend-relative delete above never matches those, and
        # ``os.remove`` still can - the file must not survive just
        # because its row predates the current layout.
        try:
            path = str(storage_path)
            if os.path.isabs(path) and os.path.exists(path):
                os.remove(path)
                logger.info(
                    f"DSAR: scrubbed export file (local fallback) "
                    f"for request {request.get('id')} at {storage_path}"
                )
        except Exception as e:
            logger.warning(
                f"DSAR: local fallback delete failed for "
                f"request {request.get('id')} at {storage_path}: {e}"
            )
