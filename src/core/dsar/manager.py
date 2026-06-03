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
