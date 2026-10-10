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
        try:
            is_valid, count, error = audit_log.verify_chain()
        except RuntimeError as chain_err:
            # rotate_on_broken_chain now FAILS LOUDLY (raises) instead of
            # silently resetting to a clean bill of health. Treat it like
            # any other invalid-chain result: halt the harvester loudly.
            is_valid, count, error = False, 0, str(chain_err)
        audit_config = self._config.get("audit_log", {})
        halt_on_invalid = audit_config.get("halt_on_invalid_audit", True)

        if not is_valid:
            # SECURITY: a real chain break IS serious (tamper-evident
            # failure of an audit log) but raising SystemExit(1) takes
            # the entire server down — operators can't examine what's
            # wrong, HTTP traffic stops, and a buggy pre-fix chain race
            # (or a deliberate GDPR-purge rotation) becomes a full
            # outage.  We now:
            #   - log CRITICAL every time;
            #   - if halt_on_invalid_audit is True, set a class-level
            #     halt flag so the harvester does NOT start its
            #     background thread (so we don't write NEW events to a
            #     known-broken chain), but the rest of the server boot
            #     keeps running so the operator can debug;
            #   - if halt_on_invalid_audit is False, log WARNING and
            #     proceed so operators can override on purpose
            #     (recovery from a known pre-fix race).
            msg = f"HARVESTER HALTED: Audit log integrity check failed! {error}"
            if halt_on_invalid:
                logger.critical(
                    f"{msg} - background export thread disabled; "
                    "server will continue to boot so the operator can inspect."
                )
                self._audit_halted = True
                return
            logger.warning(
                f"DSAR audit check failed but halt_on_invalid_audit=False. "
                f"Proceeding: {error}"
            )

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
            # HALT-FLAG GUARD: if ``start()`` flipped
            # ``self._audit_halted`` (chain-integrity failed at boot)
            # we MUST not run a harvest cycle, otherwise a new export
            # row would be appended to a known-broken chain.  Operators
            # reset by rotating the broken file aside and restarting.
            if getattr(self, "_audit_halted", False):
                logger.warning(
                    "DSAR Harvester: audit log chain is halted; skipping harvest cycle."
                )
                time.sleep(min(interval, 60))
                continue
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
        # HALT-FLAG GUARD: refuse to run when the chain-integrity
        # check failed at startup so this dsar export log cannot be
        # extended against a known-broken chain.  Belt-and-braces with
        # the ``_run_forever`` guard so direct invocations (tests,
        # admin trigger, cron) are also no-ops.
        if getattr(self, "_audit_halted", False):
            logger.warning(
                "DSAR Harvester: ``harvest()`` called while audit chain is halted; refusing to run."
            )
            return
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
        """Expire ready requests whose retention window has passed.

        SECURITY (M4): expiring must also DELETE the underlying export
        file. Flipping the row alone left full PII archives on disk
        forever for users who never polled their download - a GDPR
        retention time-bomb.
        """
        try:
            now = int(time.time())
            expired = self._db.fetch_all(
                """
                SELECT * FROM dsar_requests
                WHERE status = 'ready' AND expires_at < ?
                """,
                (now,),
            )
            for req in expired:
                try:
                    from .manager import DSARManager

                    DSARManager(self._db)._scrub_export_files(dict(req))
                except Exception as scrub_err:
                    logger.error(
                        f"Harvester: failed to delete expired export "
                        f"file for request {req.get('id')}: {scrub_err}"
                    )
                self._db.execute(
                    "UPDATE dsar_requests SET status = 'expired' WHERE id = ?",
                    (req["id"],),
                )
            if expired:
                logger.info(
                    f"Harvester: expired and deleted files for "
                    f"{len(expired)} DSAR requests"
                )
            self._sweep_orphaned_files()
        except Exception as e:
            logger.error(f"Harvester: Failed to cleanup expired requests: {e}")

    def _sweep_orphaned_files(self):
        """Delete orphaned export files from local storage.

        A file is orphaned when it sits in the DSAR export directory,
        is older than the maximum retention window, and is not
        referenced by any dsar_requests.storage_path. Only meaningful
        for the local storage backend.
        """
        try:
            backend = config.get("media", {}).get("storage_backend", "local")
            if backend != "local":
                return

            from pathlib import Path

            default_local = str(
                Path.home() / ".plexichat" / "data" / "exports" / "dsar"
            )
            base = Path(config.get("dsar", {}).get("local_path", default_local))
            if not base.is_dir():
                return

            referenced = {
                row["storage_path"]
                for row in self._db.fetch_all(
                    """
                    SELECT DISTINCT storage_path FROM dsar_requests
                    WHERE storage_path IS NOT NULL
                    """
                )
                or []
            }

            max_retention_days = max(7, self._harvester_config.get("retention_days", 7))
            cutoff = time.time() - ((max_retention_days + 1) * 86400)

            removed = 0
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(base)).replace("\\", "/")
                # Referenced paths are stored as f"dsar/{date}/name.ext";
                # match on the trailing portion so date-dir prefixes line up.
                if any(str(ref).endswith(rel) for ref in referenced):
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError as oe:
                    logger.warning(
                        f"Harvester: failed to remove orphaned export {path}: {oe}"
                    )
            if removed:
                logger.info(f"Harvester: swept {removed} orphaned DSAR export file(s)")
        except Exception as e:
            logger.warning(f"Harvester: orphan sweep skipped: {e}")

    def _process_request(self, request: dict):
        """Process a single DSAR request."""
        request_id = request["id"]
        user_id = request["user_id"]
        export_format = request.get("format", "json")

        logger.info(
            f"Harvester: Processing DSAR request {request_id} for user {user_id}"
        )

        # SECURITY (M3): the transition to 'generating' must be
        # conditional. If the user cancelled while this request sat in
        # the queue, rowcount is 0 and we MUST NOT resurrect it.
        cursor = self._db.execute(
            "UPDATE dsar_requests SET status = 'generating' "
            "WHERE id = ? AND status IN ('approved', 'pending')",
            (request_id,),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            logger.info(
                f"Harvester: request {request_id} no longer approved "
                f"(cancelled/denied); skipping generation"
            )
            return

        storage_path = None
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

            # SECURITY (M3): the completion update must be conditional on
            # the row still being 'generating'. If the user cancelled
            # while generation ran, rowcount is 0 - we then delete the
            # freshly written artifact instead of resurrecting a revoked
            # request as a live downloadable export.
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
                    f"Harvester: request {request_id} was cancelled during "
                    f"generation; deleting generated artifact"
                )
                generator.delete(storage_path)
                return

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
            # Guard the failure transition too: never overwrite a
            # cancelled row with 'failed'.
            self._db.execute(
                """
                UPDATE dsar_requests
                SET status = 'failed', error_message = ?
                WHERE id = ? AND status = 'generating'
                """,
                (str(e), request_id),
            )
            if storage_path:
                try:
                    ExportFormatGenerator(db=self._db).delete(storage_path)
                except Exception:
                    pass

            from .audit_log import DSARLog

            audit_log = DSARLog()
            audit_log.log_event(
                user_id,
                "FAILED",
                f"user:{user_id}",
                {"request_id": request_id, "error": str(e)},
            )

            raise
