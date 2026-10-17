import os
import time
from typing import List, Optional, Dict, Any

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id


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
        """Admin lists all DSAR requests with optional status filter."""
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
        longer exist on disk. We try to delete from the configured
        storage backend (local, S3, or RustFS) and log the outcome.

        The ``storage_backend`` value is consumed below when we
        decide between local-fallback (``os.remove``) and the
        remote-object delete path (S3/RustFS via the configured
        storage client). Reading the value up-front also lets the
        operator inspect logs about which backend was used.
        """
        storage_backend = request.get("storage_backend")
        storage_path = request.get("storage_path")
        if not storage_path:
            return

        # Remote-object deletion attempt (S3/RustFS) is independent of
        # the local fallback below: a failure importing the media/api
        # module must never skip the local file removal.
        if storage_backend in {"s3", "rustfs"}:
            try:
                import src.api as _dsar_api

                media_mod = _dsar_api.get_media()
                s3_client = None
                if media_mod is not None and hasattr(media_mod, "get_s3_client"):
                    s3_client = media_mod.get_s3_client()
                elif media_mod is not None and hasattr(media_mod, "_s3_client"):
                    s3_client = media_mod._s3_client
                if s3_client is not None and hasattr(s3_client, "delete_object"):
                    _bucket = None
                    for _attr in (
                        "bucket_name",
                        "_s3_bucket",
                        "_bucket",
                    ):
                        _candidate = getattr(media_mod, _attr, None)
                        if _candidate:
                            _bucket = _candidate
                            break
                    if (
                        not _bucket
                        and media_mod is not None
                        and hasattr(media_mod, "get_bucket")
                    ):
                        try:
                            _bucket = media_mod.get_bucket()
                        except Exception:
                            _bucket = None
                    if _bucket:
                        s3_client.delete_object(Bucket=_bucket, Key=storage_path)
                    else:
                        logger.warning(
                            "DSAR: cannot scrub S3 export for "
                            f"request {request.get('id')}: no "
                            "bucket getter on media module"
                        )
                    logger.info(
                        "DSAR: scrubbed remote export object for "
                        f"request {request.get('id')} at {storage_path}"
                    )
            except Exception as e:
                logger.warning(
                    "DSAR: failed to scrub remote export object "
                    f"for request {request.get('id')}: {e}"
                )

        # Local-fallback removal: always attempted regardless of the
        # configured backend or the health of the api/media modules.
        try:
            if os.path.exists(str(storage_path)):
                os.remove(str(storage_path))
                logger.info(
                    f"DSAR: scrubbed export file (local fallback) "
                    f"for request {request.get('id')} at "
                    f"{storage_path}"
                )
            elif storage_backend in {"s3", "rustfs", "remote"}:
                logger.warning(
                    "DSAR: remote storage_backend delete not "
                    "yet implemented; object remains in remote "
                    "store for request "
                    f"{request.get('id')}"
                )
        except Exception as e:
            logger.warning(
                f"DSAR: local fallback delete failed for "
                f"request {request.get('id')} at "
                f"{storage_path}: {e}"
            )
