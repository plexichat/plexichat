import time
import threading

import utils.config as config
import utils.logger as logger
from .deletion_log import DeletionLog


class AccountReaper:
    """
    Automated background task for permanent account erasure and rollback protection.
    """

    def __init__(self, db, auth_module=None):
        self._db = db
        self._auth = auth_module
        self._log = DeletionLog()
        self._config = config.get("authentication.account_deletion", {})
        self._reaper_config = self._config.get("reaper", {})
        self._is_running = False
        self._thread = None

    def start(self):
        """Start the background reaper task."""
        if self._is_running:
            return

        if not self._config.get("enabled", True):
            logger.info("Account Reaper is disabled in configuration")
            return

        # Integrity Check: Verify the hash chain before doing anything
        is_valid, count, error = self._log.verify_chain()
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
            #     halt flag so the reaper does NOT start its background
            #     thread (so we don't write NEW events to a known-broken
            #     chain), but the rest of the server boot keeps
            #     running so the operator can debug;
            #   - if halt_on_invalid_audit is False, log WARNING and
            #     proceed so operators can override on purpose
            #     (recovery from a known pre-fix race).
            msg = f"REAPER HALTED: Audit log integrity check failed! {error}"
            if halt_on_invalid:
                logger.critical(
                    f"{msg} - background purge thread disabled; "
                    "server will continue to boot so the operator can inspect."
                )
                self._audit_halted = True
                return
            logger.warning(
                f"deletion_log audit check failed but "
                f"halt_on_invalid_audit=False. Proceeding: {error}"
            )

        if count > 0:
            logger.info(f"Audit log verified: {count} records intact.")

        # Rollback protection: Check if we need to re-freeze accounts after a DB restore
        if self._reaper_config.get("boot_check_enabled", True):
            self.perform_rollback_protection_check()

        self._is_running = True
        self._thread = threading.Thread(
            target=self._run_forever, daemon=True, name="AccountReaper"
        )
        self._thread.start()
        logger.info("Account Reaper background task started")

    def stop(self):
        self._is_running = False

    def perform_rollback_protection_check(self):
        """
        Compares the external audit log with the database.
        If a user is scheduled for deletion in the log but 'active' in the DB,
        it re-freezes them immediately (handles DB rollbacks).
        """
        logger.info("Reaper: Performing rollback protection check...")
        scheduled = self._log.get_scheduled_deletions()
        if not scheduled:
            return

        for user_id, entry in scheduled.items():
            try:
                # Check current status in DB
                user_row = self._db.fetch_one(
                    "SELECT deletion_status FROM auth_users WHERE id = ?", (user_id,)
                )
                if user_row and user_row["deletion_status"] == "active":
                    logger.warning(
                        f"Reaper: Detected 'zombie' account {user_id} (active in DB but scheduled in log). Re-freezing..."
                    )

                    # Re-apply the freeze. The audit-log entry timestamp is
                    # in SECONDS while the deletion_at column is stored in
                    # MILLISECONDS, so convert before adding the grace
                    # period (also expressed in ms).
                    scheduled_at = entry["timestamp"]
                    grace_days = self._config.get("grace_period_days", 30)
                    deletion_at = self._rollback_deletion_at(
                        entry, scheduled_at, grace_days
                    )

                    self._db.execute(
                        "UPDATE auth_users SET deletion_status = 'frozen', deletion_at = ? WHERE id = ?",
                        (deletion_at, user_id),
                    )

                    # Clear sessions again for safety
                    if self._auth:
                        self._auth.logout_all(user_id)
            except Exception as e:
                logger.error(
                    f"Reaper: Rollback protection failed for user {user_id}: {e}"
                )

    @staticmethod
    def _rollback_deletion_at(entry: dict, scheduled_at: int, grace_days: int) -> int:
        """Resolve the ms-epoch ``deletion_at`` for a rollback re-freeze.

        The log entry ``timestamp`` is SECONDS while the ``deletion_at``
        column is MILLISECONDS. Prefer the authoritative ms ``deletion_at``
        recorded in the entry metadata (written by ``schedule`` and refreshed
        by ``DELAYED`` events) so an admin delay survives a DB restore;
        otherwise derive it from the log timestamp plus the grace period.
        Seconds-scale values are normalized to ms defensively.
        """
        try:
            metadata = entry.get("metadata") or {}
        except AttributeError:
            metadata = {}
        candidate = metadata.get("deletion_at") if isinstance(metadata, dict) else None
        if isinstance(candidate, bool):
            candidate = None
        if isinstance(candidate, (int, float)):
            candidate = int(candidate)
            if 0 < candidate < 100_000_000_000:
                # Seconds-scale epoch (admin-supplied or legacy) -> ms.
                candidate *= 1000
            if candidate > 0:
                return candidate
        return (int(scheduled_at) * 1000) + (int(grace_days) * 86400 * 1000)

    def _run_forever(self):
        interval = self._reaper_config.get("interval_hours", 24) * 3600
        while self._is_running:
            # HALT-FLAG GUARD: ``start()`` flips ``self._audit_halted``
            # when the chain could not be verified and the operator did
            # not override ``halt_on_invalid_audit``. Treat every cycle
            # entry as a no-op so the daemon never writes NEW rows to a
            # known-broken chain (which would silently extend a corrupt
            # history).  Operators can still re-init via a config flip
            # + restart without losing the halted signal otherwise.
            if getattr(self, "_audit_halted", False):
                logger.warning(
                    "Reaper: audit log chain is halted; skipping harvest cycle."
                )
                time.sleep(min(interval, 60))
                continue
            try:
                self.harvest()
            except Exception as e:
                logger.error(f"Reaper: Harvest cycle failed: {e}", exc_info=True)

            # Sleep in increments to allow faster shutdown
            for _ in range(int(interval / 10)):
                if not self._is_running:
                    break
                time.sleep(10)

    def harvest(self):
        """
        Main purge logic. Identifies and erases users past their grace period.
        Also cleans up expired passkey challenges.
        """
        # HALT-FLAG GUARD: skip every harvest cycle when the chain was
        # rejected at startup so we cannot write new rows against a
        # broken chain.  This sits alongside the ``_run_forever`` guard
        # so a direct invocation of ``harvest()`` (e.g. tests, cron,
        # admin trigger) is also a no-op.
        if getattr(self, "_audit_halted", False):
            logger.warning(
                "Reaper: ``harvest()`` called while audit chain is halted; refusing to run."
            )
            return
        # deletion_at is stored in MILLISECONDS (matching
        # DeletionMixin.schedule_account_deletion); compare in the same unit.
        now = int(time.time() * 1000)
        # SQLite reads ``LIMIT -1`` as *unlimited*, so a non-positive
        # configured ``batch_size`` would purge every frozen account in a
        # single cycle (and ``LIMIT 0`` would stall purges forever). Clamp to
        # at least one account per cycle.
        batch_size = max(1, int(self._reaper_config.get("batch_size", 50)))

        # Clean up expired passkey challenges and Suite download tickets.
        self._cleanup_passkey_challenges()
        self._cleanup_suite_tickets()

        # Find users ready for purge
        purge_list = self._db.fetch_all(
            "SELECT id, username FROM auth_users WHERE deletion_status = 'frozen' AND deletion_at <= ? LIMIT ?",
            (now, batch_size),
        )

        if not purge_list:
            return

        logger.info(f"Reaper: Beginning harvest for {len(purge_list)} accounts")

        for user in purge_list:
            self.purge_user(user["id"], user["username"])

    def _cleanup_suite_tickets(self):
        """Remove redeemed/expired PlexiStash tickets when Suite is installed."""
        try:
            from src.core.suite.stash import StashManager

            removed = StashManager(self._db).cleanup_expired_tickets()
            if removed:
                logger.debug("Reaper: Cleaned up %d expired Suite ticket(s)", removed)
        except Exception as exc:
            # Suite is optional and its tables may not exist on older databases.
            logger.debug("Reaper: Suite ticket cleanup skipped: %s", exc)

    def _cleanup_passkey_challenges(self):
        """Clean up expired passkey challenges."""
        try:
            now = int(time.time() * 1000)  # milliseconds
            result = self._db.execute(
                "DELETE FROM auth_passkey_challenges WHERE expires_at < ?",
                (now,),
            )
            if result.rowcount > 0:
                logger.debug(
                    f"Reaper: Cleaned up {result.rowcount} expired passkey challenges"
                )
        except Exception as e:
            logger.error(f"Reaper: Failed to cleanup passkey challenges: {e}")

    def _purge_suite_data(self, user_id: int) -> list:
        """Erase user-owned Suite rows before removing the auth user.

        Suite tables are optional on older databases, so each statement is
        isolated. Child rows are removed before their parents for PostgreSQL
        installations with foreign keys enabled.

        PlexiSum workbook *content* lives in the blob store (``.plexisum``
        files) rather than the main database, so the Sum manager erases the
        files and its own metadata via ``purge_user_data`` when the module is
        initialized; the row-level deletes here are the schema-tolerant
        fallback for the remaining Suite tables.

        Returns the list of blob paths that could NOT be swept from external
        storage (empty when the managed purge ran or the user owned no
        workbooks). A non-empty return MUST be recorded by the caller: the
        GDPR row erasure still completes, but an operator blob sweep is due.
        """
        # Remove .plexisum files and Sum metadata through the manager so
        # workbook blobs do not leak on the storage adapter. This must run
        # before the row-level deletes below, which otherwise empty
        # sum_workbooks first and leave the files orphaned.
        #
        # Phase 1 — acquire a manager WITHOUT failing: any acquisition
        # problem (suite unimportable, manager never initialized, boot-time
        # setup unavailable) lands in ``manager_error`` and drops to the
        # row-level fallback. A genuine storage failure from an ACQUIRED
        # manager (phase 2) still raises so the cycle retries with the blob
        # pointers intact.
        sum_domain = None
        manager = None
        manager_error: Exception | None = None
        try:
            from src.core.suite import sum as sum_domain
        except Exception as exc:
            manager_error = exc
        else:
            try:
                manager = sum_domain.get_manager()
            except Exception:
                # Initialize through the same compatibility path as normal
                # boot before erasing any Sum rows.
                try:
                    import src.api as api

                    db = api.get_db()
                    if db is None:
                        raise RuntimeError("PlexiSum database unavailable")
                    manager = sum_domain.setup(
                        db,
                        servers_module=api.get_servers(),
                        media=api.get_media(),
                    )
                except Exception as exc:
                    manager = None
                    manager_error = exc

        if manager is not None and sum_domain is not None:
            # A configured Sum manager must be able to erase its external
            # workbook blobs before metadata is removed. Do not continue with
            # row deletion after a real storage failure, or the blob paths are
            # lost and the user's .plexisum files become orphaned.
            try:
                manager.purge_user_data(user_id)
            except Exception as exc:
                logger.error("Reaper: PlexiSum blob purge failed: %s", exc)
                raise
            unswept: list = []
        else:
            # The Sum manager is not initialized in this process (test,
            # self-test, or degraded boot) so no storage adapter can sweep
            # the external .plexisum blobs. Failing the whole GDPR erasure
            # here would leave the account frozen forever (scheduled
            # deletions never reaped); instead erase the metadata rows below
            # and return the blob paths so the caller records an operator
            # sweep.
            try:
                has_sum_tables = self._db.table_exists("sum_workbooks")
            except Exception as table_exc:
                # If the database cannot answer, assume the tables MAY exist
                # and run the (individually guarded, schema-tolerant) row
                # fallback below rather than skipping Sum erasure entirely.
                logger.debug(
                    "Reaper: Sum table probe failed (%s); attempting row fallback",
                    table_exc,
                )
                has_sum_tables = True
            if not has_sum_tables:
                logger.debug("Reaper: PlexiSum is not installed; skipping purge")
                unswept = []
            else:
                unswept = self._purge_sum_rows_fallback(
                    user_id, manager_error or RuntimeError("PlexiSum unavailable")
                )

        statements = (
            ("DELETE FROM suite_audit_log WHERE actor_id = ?", (user_id,)),
            (
                "DELETE FROM suite_audit_log WHERE resource_id IN "
                "(SELECT id FROM stash_items WHERE owner_id = ?)",
                (user_id,),
            ),
            ("DELETE FROM stash_download_tickets WHERE subject_id = ?", (user_id,)),
            (
                "DELETE FROM stash_grants WHERE created_by = ? OR "
                "(subject_type = 'user' AND subject_id = ?) OR item_id IN "
                "(SELECT id FROM stash_items WHERE owner_id = ?)",
                (user_id, str(user_id), user_id),
            ),
            (
                "DELETE FROM stash_versions WHERE created_by = ? OR item_id IN "
                "(SELECT id FROM stash_items WHERE owner_id = ?)",
                (user_id, user_id),
            ),
            ("DELETE FROM stash_items WHERE owner_id = ?", (user_id,)),
            ("DELETE FROM stash_collections WHERE owner_id = ?", (user_id,)),
        )
        for query, params in statements:
            try:
                self._db.execute(query, params)
            except Exception as exc:
                logger.debug("Reaper: optional Suite purge skipped: %s", exc)

        return unswept

    def _purge_sum_rows_fallback(self, user_id: int, cause: Exception) -> list:
        """Row-level Sum erasure when no Sum manager/storage adapter exists.

        Deletes the user's workbook metadata (child rows cascade from
        ``sum_workbooks`` via ``ON DELETE CASCADE``) and grants where the
        user is a subject on other people's workbooks. External ``.plexisum``
        blobs cannot be swept without the storage adapter, so their paths
        are collected and returned for an operator sweep (see ``purge_user``,
        which records them on the PURGED audit event).

        Every statement is individually guarded: the fallback is
        schema-tolerant across Suite versions and never vetoes the core
        account erasure.
        """
        blob_paths: list = []
        workbook_ids: list = []

        def _collect(query: str, params: tuple) -> None:
            try:
                rows = self._db.fetch_all(query, params)
            except Exception as exc:
                logger.debug("Reaper: Sum blob-path probe skipped: %s", exc)
                return
            for row in rows or []:
                try:
                    path = row.get("blob_path") if isinstance(row, dict) else row[0]
                except Exception:
                    continue
                if path:
                    blob_paths.append(path)

        try:
            try:
                rows = self._db.fetch_all(
                    "SELECT id, blob_path FROM sum_workbooks WHERE owner_id = ?",
                    (user_id,),
                )
            except Exception as exc:
                logger.debug("Reaper: Sum workbook probe skipped: %s", exc)
                rows = []
            for row in rows or []:
                try:
                    if isinstance(row, dict):
                        workbook_ids.append(row["id"])
                        if row.get("blob_path"):
                            blob_paths.append(row["blob_path"])
                    else:
                        workbook_ids.append(row[0])
                        if row[1]:
                            blob_paths.append(row[1])
                except Exception:
                    continue
            if workbook_ids:
                placeholders = ",".join("?" * len(workbook_ids))
                ids = tuple(workbook_ids)
                _collect(
                    "SELECT blob_path FROM sum_snapshots WHERE "
                    f"workbook_id IN ({placeholders})",
                    ids,
                )
                _collect(
                    "SELECT blob_path FROM sum_revision_blobs WHERE "
                    f"workbook_id IN ({placeholders})",
                    ids,
                )
        except Exception as exc:
            logger.debug("Reaper: Sum blob-path collection skipped: %s", exc)

        for query, params in (
            ("DELETE FROM sum_grants WHERE subject_id = ?", (user_id,)),
            ("DELETE FROM sum_workbooks WHERE owner_id = ?", (user_id,)),
        ):
            try:
                self._db.execute(query, params)
            except Exception as exc:
                logger.debug("Reaper: Sum row fallback skipped: %s", exc)

        if blob_paths or workbook_ids:
            logger.error(
                "Reaper: purged user %s without a Sum storage adapter "
                "(%s); erased %d workbook row(s) but %d external blob(s) "
                "need an operator sweep: %s",
                user_id,
                cause,
                len(workbook_ids),
                len(blob_paths),
                ", ".join(str(p) for p in blob_paths[:10]),
            )
        else:
            logger.debug(
                "Reaper: Sum row fallback for user %s: no workbooks owned (%s)",
                user_id,
                cause,
            )
        return blob_paths

    def purge_user(self, user_id: int, username: str):
        """
        Performs a full idempotent erasure of a user account.
        """
        logger.info(f"Reaper: Purging user {user_id} ({username})")

        try:
            # 1. Anonymize Messages (author_id has NOT NULL constraint, keep it)
            if self._config.get("anonymize_content", True):
                self._db.execute(
                    "UPDATE msg_messages SET content = '[This message was sent by a deleted user]' WHERE author_id = ?",
                    (user_id,),
                )
            else:
                self._db.execute(
                    "DELETE FROM msg_messages WHERE author_id = ?", (user_id,)
                )

            # 2. Scrub Media (Avatars, Attachments)
            try:
                from src.core import avatars

                avatars.delete_user_avatar(user_id)
            except Exception as me:
                logger.warning(
                    f"Reaper: Failed to scrub some media for {user_id}: {me}"
                )

            # 2b. Scrub/delete artifact & transcript data (voice calls, call
            # artifacts, transcripts, and collaborative ops).
            try:
                from src.core.artifacts.privacy import anonymize_user_artifacts

                anonymize_user_artifacts(self._db, user_id, self._config)
            except Exception as ae:
                logger.warning(
                    f"Reaper: Failed to scrub artifact data for {user_id}: {ae}"
                )

            # 3. Final Database Erasure
            # Order matters: child tables before parent tables to respect FK constraints.
            # The Suite sweep returns external blob paths it could not delete
            # (manager-unavailable fallback); they are recorded on the PURGED
            # audit event below so operators can sweep them.
            unswept_suite_blobs = self._purge_suite_data(user_id)
            self._db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            self._db.execute("DELETE FROM auth_devices WHERE user_id = ?", (user_id,))
            self._db.execute(
                "DELETE FROM rel_friends WHERE user_id = ? OR friend_id = ?",
                (user_id, user_id),
            )
            self._db.execute(
                "DELETE FROM rel_blocked WHERE blocker_id = ? OR blocked_id = ?",
                (user_id, user_id),
            )
            self._db.execute(
                "DELETE FROM rel_friend_requests WHERE sender_id = ? OR recipient_id = ?",
                (user_id, user_id),
            )
            self._db.execute(
                "DELETE FROM admin_role_assignments WHERE admin_id = ?",
                (user_id,),
            )
            self._db.execute(
                "DELETE FROM srv_member_roles WHERE member_id IN (SELECT id FROM srv_members WHERE user_id = ?)",
                (user_id,),
            )
            self._db.execute("DELETE FROM srv_members WHERE user_id = ?", (user_id,))
            self._db.execute(
                "DELETE FROM srv_onboarding_progress WHERE user_id = ?", (user_id,)
            )
            # Tables without ON DELETE CASCADE on auth_users FK must be deleted explicitly
            # Delete admin_notes first (references feedback.ticket_id)
            # Use a two-step approach to avoid foreign key constraint issues
            try:
                # First, get all feedback IDs for this user
                feedback_ids = self._db.fetch_all(
                    "SELECT id FROM feedback WHERE user_id = ?", (user_id,)
                )
                if feedback_ids:
                    # Extract IDs from rows (handle both dict and tuple formats)
                    ids_to_delete = []
                    for row in feedback_ids:
                        if isinstance(row, dict):
                            ids_to_delete.append(row["id"])
                        else:
                            ids_to_delete.append(row[0])

                    # Delete admin_notes for these feedback IDs
                    if ids_to_delete:
                        placeholders = ",".join("?" * len(ids_to_delete))
                        self._db.execute(
                            f"DELETE FROM admin_notes WHERE ticket_id IN ({placeholders})",
                            tuple(ids_to_delete),
                        )
            except Exception as e:
                logger.warning(
                    f"Reaper: Failed to delete admin_notes for user {user_id}: {e}"
                )

            # Now delete feedback entries
            self._db.execute("DELETE FROM feedback WHERE user_id = ?", (user_id,))
            self._db.execute(
                "DELETE FROM message_reports WHERE reporter_id = ? OR reported_user_id = ?",
                (user_id, user_id),
            )
            if self._db.column_exists("admin_audit_log", "target_user_id"):
                self._db.execute(
                    "DELETE FROM admin_audit_log WHERE admin_id = ? OR target_user_id = ?",
                    (user_id, user_id),
                )
            else:
                self._db.execute(
                    "DELETE FROM admin_audit_log WHERE admin_id = ?",
                    (user_id,),
                )
            self._db.execute(
                "DELETE FROM media_hash_reports WHERE reporter_id = ?", (user_id,)
            )
            self._db.execute("DELETE FROM auth_users WHERE id = ?", (user_id,))
            self._db.execute(
                "DELETE FROM auth_deletion_records WHERE user_id = ?", (user_id,)
            )

            # 4. Final Log Entry
            purged_metadata: dict = {"username": username}
            if unswept_suite_blobs:
                # Cap the stored paths; the count is authoritative.
                purged_metadata["suite_blobs_unswept"] = len(unswept_suite_blobs)
                purged_metadata["suite_blob_paths"] = [
                    str(p) for p in unswept_suite_blobs[:50]
                ]
            self._log.log_event(user_id, "PURGED", f"user:{user_id}", purged_metadata)

            logger.info(f"Reaper: Successfully purged user {user_id}")
        except Exception as e:
            logger.error(
                f"Reaper: Purge failed for user {user_id}: {e}. "
                f"User will be picked up in next harvest cycle.",
                exc_info=True,
            )
