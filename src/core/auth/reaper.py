
import time
import asyncio
import threading
from typing import Optional, List, Dict, Any

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
        if not is_valid:
            logger.critical(f"REAPER HALTED: Audit log integrity check failed! {error}")
            return
        
        if count > 0:
            logger.info(f"Audit log verified: {count} records intact.")

        # Rollback protection: Check if we need to re-freeze accounts after a DB restore
        if self._reaper_config.get("boot_check_enabled", True):
            self.perform_rollback_protection_check()

        self._is_running = True
        self._thread = threading.Thread(target=self._run_forever, daemon=True, name="AccountReaper")
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
                user_row = self._db.fetch_one("SELECT deletion_status FROM auth_users WHERE id = ?", (user_id,))
                if user_row and user_row["deletion_status"] == "active":
                    logger.warning(f"Reaper: Detected 'zombie' account {user_id} (active in DB but scheduled in log). Re-freezing...")
                    
                    # Re-apply the freeze
                    scheduled_at = entry["timestamp"]
                    grace_days = self._config.get("grace_period_days", 30)
                    deletion_at = scheduled_at + (grace_days * 86400)
                    
                    self._db.execute(
                        "UPDATE auth_users SET deletion_status = 'frozen', deletion_at = ? WHERE id = ?",
                        (deletion_at, user_id)
                    )
                    
                    # Clear sessions again for safety
                    if self._auth:
                        self._auth.logout_all(user_id)
            except Exception as e:
                logger.error(f"Reaper: Rollback protection failed for user {user_id}: {e}")

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
        """
        now = int(time.time())
        batch_size = self._reaper_config.get("batch_size", 50)
        
        # Find users ready for purge
        purge_list = self._db.fetch_all(
            "SELECT id, username FROM auth_users WHERE deletion_status = 'frozen' AND deletion_at <= ? LIMIT ?",
            (now, batch_size)
        )
        
        if not purge_list:
            return

        logger.info(f"Reaper: Beginning harvest for {len(purge_list)} accounts")
        
        for user in purge_list:
            self.purge_user(user["id"], user["username"])

    def purge_user(self, user_id: int, username: str):
        """
        Performs a full idempotent erasure of a user account.
        """
        logger.info(f"Reaper: Purging user {user_id} ({username})")
        
        try:
            # 1. Anonymize Messages
            if self._config.get("anonymize_content", True):
                self._db.execute(
                    "UPDATE msg_messages SET content = '[This message was sent by a deleted user]', author_id = NULL WHERE author_id = ?",
                    (user_id,)
                )
            else:
                self._db.execute("DELETE FROM msg_messages WHERE author_id = ?", (user_id,))

            # 2. Scrub Media (Avatars, Attachments)
            try:
                from src.core import avatars, media
                avatars.delete_user_avatar(user_id)
                # Note: Add media.delete_all_user_media(user_id) if it exists
            except Exception as me:
                logger.warning(f"Reaper: Failed to scrub some media for {user_id}: {me}")

            # 3. Final Database Erasure
            # Remove from all related tables (sessions, relationships, etc.)
            self._db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            self._db.execute("DELETE FROM auth_devices WHERE user_id = ?", (user_id,))
            self._db.execute("DELETE FROM rel_friends WHERE user_id = ? OR friend_id = ?", (user_id, user_id))
            self._db.execute("DELETE FROM srv_members WHERE user_id = ?", (user_id,))
            self._db.execute("DELETE FROM auth_users WHERE id = ?", (user_id,))
            self._db.execute("DELETE FROM auth_deletion_records WHERE user_id = ?", (user_id,))

            # 4. Final Log Entry
            self._log.log_event(user_id, "PURGED", f"user:{user_id}", {"username": username})
            
            logger.info(f"Reaper: Successfully purged user {user_id}")
            
        except Exception as e:
            logger.error(f"Reaper: Failed to purge user {user_id}: {e}", exc_info=True)
            # User will be picked up in the next harvest cycle as deletion_status is still 'frozen'
