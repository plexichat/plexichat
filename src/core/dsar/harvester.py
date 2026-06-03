import time
import threading
from typing import Optional

import utils.config as config
import utils.logger as logger
from .collector import DataCollector
from .export_formats import ExportFormatGenerator


class DSARHarvester:
    """
    Automated background worker for processing DSAR requests.
    Analogous to AccountReaper but for data exports instead of deletions.
    """

    def __init__(self, db, config_override: Optional[dict] = None):
        self._db = db
        self._config = config_override or config.get("dsar", {})
        self._harvester_config = self._config.get("harvester", {})
        self._is_running = False
        self._thread = None

    def start(self):
        """Start the background harvester task."""
        if self._is_running:
            return

        if not self._config.get("enabled", True):
            logger.info("DSAR Harvester is disabled in configuration")
            return

        from .audit_log import DSARLog

        audit_log = DSARLog()
        is_valid, count, error = audit_log.verify_chain()
        audit_config = self._config.get("audit_log", {})
        halt_on_invalid = audit_config.get("halt_on_invalid_audit", True)

        if not is_valid:
            msg = f"HARVESTER HALTED: Audit log integrity check failed! {error}"
            if halt_on_invalid:
                logger.critical(msg)
                raise SystemExit(1)
            logger.error(msg)
            return

        if count > 0:
            logger.info(f"DSAR Audit log verified: {count} records intact.")

        self._is_running = True
        self._thread = threading.Thread(
            target=self._run_forever, daemon=True, name="DSARHarvester"
        )
        self._thread.start()
        logger.info("DSAR Harvester background task started")

    def stop(self):
        self._is_running = False

    def _run_forever(self):
        interval = self._harvester_config.get("interval_hours", 1) * 3600
        while self._is_running:
            try:
                self.harvest()
            except Exception as e:
                logger.error(f"Harvester: Harvest cycle failed: {e}", exc_info=True)

            for _ in range(int(interval / 10)):
                if not self._is_running:
                    break
                time.sleep(10)

    def harvest(self):
        """
        Main processing logic for DSAR requests.
        """
        batch_size = self._harvester_config.get("batch_size", 10)

        self._cleanup_expired()

        require_admin = self._harvester_config.get("require_admin_review", True)

        if require_admin:
            requests = self._db.fetch_all(
                """
                SELECT * FROM dsar_requests
                WHERE status = 'approved'
                ORDER BY requested_at ASC
                LIMIT ?
                """,
                (batch_size,),
            )
        else:
            pending = self._db.fetch_all(
                """
                SELECT * FROM dsar_requests
                WHERE status = 'pending'
                ORDER BY requested_at ASC
                LIMIT ?
                """,
                (batch_size,),
            )

            for req in pending:
                self._db.execute(
                    "UPDATE dsar_requests SET status = 'approved' WHERE id = ?",
                    (req["id"],),
                )

            requests = self._db.fetch_all(
                """
                SELECT * FROM dsar_requests
                WHERE status = 'approved'
                ORDER BY requested_at ASC
                LIMIT ?
                """,
                (batch_size,),
            )

        if not requests:
            return

        logger.info(f"Harvester: Processing {len(requests)} DSAR requests")

        for req in requests:
            try:
                self._process_request(req)
            except Exception as e:
                logger.error(
                    f"Harvester: Failed to process request {req.get('id')}: {e}",
                    exc_info=True,
                )
                self._db.execute(
                    "UPDATE dsar_requests SET status = 'failed', error_message = ? WHERE id = ?",
                    (str(e), req["id"]),
                )

    def _cleanup_expired(self):
        """Mark expired requests."""
        try:
            now = int(time.time())
            result = self._db.execute(
                """
                UPDATE dsar_requests
                SET status = 'expired'
                WHERE status = 'ready' AND expires_at < ?
                """,
                (now,),
            )
            if result.rowcount > 0:
                logger.debug(
                    f"Harvester: Marked {result.rowcount} expired DSAR requests"
                )
        except Exception as e:
            logger.error(f"Harvester: Failed to cleanup expired requests: {e}")

    def _process_request(self, request: dict):
        """Process a single DSAR request."""
        request_id = request["id"]
        user_id = request["user_id"]
        export_format = request.get("format", "json")

        logger.info(
            f"Harvester: Processing DSAR request {request_id} for user {user_id}"
        )

        self._db.execute(
            "UPDATE dsar_requests SET status = 'generating' WHERE id = ?",
            (request_id,),
        )

        try:
            collector = DataCollector(self._db)
            data = collector.collect_all(user_id)

            generator = ExportFormatGenerator(db=self._db)

            retention_days = self._harvester_config.get("retention_days", 7)
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

            from .audit_log import DSARLog

            audit_log = DSARLog()
            audit_log.log_event(
                user_id,
                "READY",
                f"user:{user_id}",
                {"request_id": request_id, "file_size": file_size},
            )

            logger.info(
                f"Harvester: Successfully generated DSAR export for request {request_id}"
            )

        except Exception as e:
            self._db.execute(
                """
                UPDATE dsar_requests
                SET status = 'failed', error_message = ?
                WHERE id = ?
                """,
                (str(e), request_id),
            )

            from .audit_log import DSARLog

            audit_log = DSARLog()
            audit_log.log_event(
                user_id,
                "FAILED",
                f"user:{user_id}",
                {"request_id": request_id, "error": str(e)},
            )

            raise
