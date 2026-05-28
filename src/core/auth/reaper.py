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
            msg = f"REAPER HALTED: Audit log integrity check failed! {error}"
            if halt_on_invalid:
                logger.critical(msg)
                raise SystemExit(1)
            logger.error(msg)
            return

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

                    # Re-apply the freeze
                    scheduled_at = entry["timestamp"]
                    grace_days = self._config.get("grace_period_days", 30)
                    deletion_at = scheduled_at + (grace_days * 86400)

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

    def _run_forever(self):
        interval = self._reaper_config.get("interval_hours", 24) * 3600
        while self._is_running:
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
        now = int(time.time())
        batch_size = self._reaper_config.get("batch_size", 50)

        # Clean up expired passkey challenges
        self._cleanup_passkey_challenges()

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

            # 3. Final Database Erasure
            # Order matters: child tables before parent tables to respect FK constraints
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
            try:
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
            except Exception:
                pass
            self._db.execute(
                "DELETE FROM media_hash_reports WHERE reporter_id = ?", (user_id,)
            )
            self._db.execute("DELETE FROM auth_users WHERE id = ?", (user_id,))
            self._db.execute(
                "DELETE FROM auth_deletion_records WHERE user_id = ?", (user_id,)
            )

            # 4. Final Log Entry
            self._log.log_event(
                user_id, "PURGED", f"user:{user_id}", {"username": username}
            )

            logger.info(f"Reaper: Successfully purged user {user_id}")
        except Exception as e:
            logger.error(
                f"Reaper: Purge failed for user {user_id}: {e}. "
                f"User will be picked up in next harvest cycle.",
                exc_info=True,
            )
