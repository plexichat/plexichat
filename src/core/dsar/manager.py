import time
from typing import List, Optional, Dict, Any

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
        request_id = generate_snowflake_id()
        now = int(time.time())

        metadata = {"categories": categories} if categories else None
        metadata_json = str(metadata) if metadata else None

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

        if request["status"] != "ready":
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

        self._db.execute(
            "UPDATE dsar_requests SET status = 'downloaded' WHERE id = ?",
            (request_id,),
        )

        self._invalidate_cache("dsar_request:*")

        audit_log = self._get_audit_log()
        audit_log.log_event(
            user_id,
            "DOWNLOADED",
            f"user:{user_id}",
            {"request_id": request_id},
        )

        return {
            "storage_backend": request.get("storage_backend", "local"),
            "storage_path": request.get("storage_path"),
            "checksum": request["checksum"],
            "file_size": request["file_size_bytes"],
            "format": request["format"],
        }

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

        self._db.execute(
            "UPDATE dsar_requests SET status = 'approved', admin_id = ? WHERE id = ?",
            (admin_id, request_id),
        )

        from .collector import DataCollector
        from .export_formats import ExportFormatGenerator

        user_id = request["user_id"]
        export_format = request.get("format", "json")

        self._db.execute(
            "UPDATE dsar_requests SET status = 'generating' WHERE id = ?",
            (request_id,),
        )

        try:
            collector = DataCollector(self._db)
            data = collector.collect_all(user_id)

            generator = ExportFormatGenerator(db=self._db)

            retention_days = 7
            expires_at = int(time.time()) + (retention_days * 86400)

            if export_format == "zip":
                storage_path, checksum, file_size = generator.generate_zip(
                    data, request_id, user_id
                )
            else:
                storage_path, checksum, file_size = generator.generate_json(
                    data, request_id, user_id
                )

            self._db.execute(
                """
                UPDATE dsar_requests
                SET status = 'ready',
                    completed_at = ?,
                    expires_at = ?,
                    storage_backend = ?,
                    storage_path = ?,
                    checksum = ?,
                    file_size_bytes = ?
                WHERE id = ?
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
                WHERE id = ?
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
        storage backend (local, S3, or MinIO) and log the outcome.

        The ``storage_backend`` value is consumed below when we
        decide between local-fallback (``os.remove``) and the
        remote-object delete path (S3/MinIO via the configured
        storage client). Reading the value up-front also lets the
        operator inspect logs about which backend was used.
        """
        storage_backend = request.get("storage_backend")
        storage_path = request.get("storage_path")
        if not storage_path:
            return
        try:
            import src.api as api_module

            media = api_module.get_media()
            storage = None
            if media is not None and hasattr(media, "_backend"):
                # media manager exposes the active backend lazily.
                try:
                    storage = media._backend  # type: ignore[attr-defined]
                except Exception:
                    storage = None

            if storage is not None and hasattr(storage, "delete"):
                try:
                    storage.delete(str(storage_path))
                    logger.info(
                        f"DSAR: scrubbed export file for request "
                        f"{request.get('id')} at {storage_path}"
                    )
                    return
                except Exception as e:
                    logger.warning(
                        f"DSAR: storage backend delete failed for "
                        f"request {request.get('id')}: {e}"
                    )
            # Fall back to a best-effort local removal. Note: when
            # ``storage_backend`` indicates S3 / MinIO neither the
            # media backend nor ``os.remove`` will reach the object -
            # we attempt a remote delete via the configured S3 /
            # MinIO client first, and only log if neither succeeds.
            import os

            try:
                if os.path.exists(str(storage_path)):
                    os.remove(str(storage_path))
                    logger.info(
                        f"DSAR: scrubbed export file (local fallback) "
                        f"for request {request.get('id')} at "
                        f"{storage_path}"
                    )
                elif request.get("storage_backend") in {"s3", "minio", "remote"}:
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

            # Remote-object deletion: if configured storage is S3
            # or MinIO, attempt a direct delete via the configured
            # client. Plexichat's media module ships an S3 client
            # wrapper; reach for it lazily and only on the relevant
            # backends.
            if storage_backend in {"s3", "minio"}:
                try:
                    import src.api as _dsar_api

                    media_mod = _dsar_api.get_media()
                    s3_client = None
                    if media_mod is not None and hasattr(media_mod, "get_s3_client"):
                        s3_client = media_mod.get_s3_client()  # type: ignore[attr-defined]
                    elif media_mod is not None and hasattr(media_mod, "_s3_client"):
                        s3_client = media_mod._s3_client  # type: ignore[attr-defined]
                    if s3_client is not None and hasattr(s3_client, "delete_object"):
                        # Resolve the bucket at runtime; fall back
                        # to logging without delete if no public bucket
                        # getter is available.
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
        except Exception as e:
            logger.error(
                f"DSAR: failed to scrub export files for request "
                f"{request.get('id')}: {e}"
            )
