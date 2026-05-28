"""
Cleanup service for SelfTestRunner.

Pre-test SQL truncation, recursive user/server deletion,
API-based cleanup, and SQL fallback.
"""

from typing import Any

import src.api as api
import utils.logger as logger

from ..context import SelfTestContext


class CleanupService:
    """Manages test data cleanup (API calls + SQL fallback)."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def pre_test_cleanup(self) -> None:
        logger.info("Performing pre-test cleanup...")
        db = api.get_db()
        if not db:
            return

        try:
            if db.type == "sqlite":
                db.execute("PRAGMA foreign_keys=OFF")

            db.begin_transaction()

            for tbl in ["media_upload_sessions"]:
                try:
                    db.execute(f"DELETE FROM {tbl}")
                except Exception:
                    pass

            truncate_tables = [
                "notif_notifications",
                "user_reports",
                "media_hash_reports",
                "message_reports",
                "feedback",
                "media_blocked_hashes",
                "media_blocked_users",
                "app_bot_requests",
                "auth_api_access_tokens",
                "auth_api_access_token_scopes",
                "poll_polls",
                "poll_options",
                "poll_votes",
                "thread_threads",
                "sticker_stickers",
                "sticker_packs",
                "admin_role_assignments",
                "admin_approvals",
                "username_blacklist",
            ]
            for tbl in truncate_tables:
                try:
                    db.execute(f"DELETE FROM {tbl}")
                except Exception:
                    pass

            try:
                orphan_srv_rows = db.fetch_all(
                    "SELECT s.id, s.owner_id FROM srv_servers s "
                    "JOIN auth_users u ON s.owner_id = u.id "
                    "WHERE u.username LIKE 'selftest_%' AND s.deleted = 0"
                )
                for os_row in orphan_srv_rows:
                    osid = os_row["id"] if isinstance(os_row, dict) else os_row[0]
                    self.delete_server_recursive(db, osid)
                if orphan_srv_rows:
                    logger.debug(
                        f"Pre-test cleanup: removed {len(orphan_srv_rows)} orphaned servers"
                    )
            except Exception:
                pass

            rows = db.fetch_all(
                "SELECT id, username FROM auth_users WHERE username LIKE 'selftest_%'"
            )
            for row in rows:
                uid = row["id"] if isinstance(row, dict) else row[0]
                uname = row["username"] if isinstance(row, dict) else row[1]
                try:
                    db.execute(
                        "UPDATE auth_users SET account_locked = 0, locked_until = NULL WHERE id = ?",
                        (uid,),
                    )
                except Exception:
                    pass
                self.delete_all_for_user(db, uid)
                logger.debug(f"Pre-test cleanup: Deleted user {uname}")

            try:
                db.execute("DELETE FROM admin_users WHERE username LIKE 'selftest_%'")
            except Exception:
                pass

            db.commit()
        except Exception as e:
            if db.in_transaction:
                try:
                    db.rollback()
                except Exception:
                    pass
            logger.warning(f"Pre-test cleanup failed (non-critical): {e}")
        finally:
            if db.type == "sqlite":
                db.execute("PRAGMA foreign_keys=ON")

    def delete_all_for_user(self, db: Any, uid: int) -> None:
        db.execute(
            "DELETE FROM message_reports WHERE reporter_id = ? OR reported_user_id = ?",
            (uid, uid),
        )
        db.execute(
            "DELETE FROM user_reports WHERE reporter_id = ? OR reported_user_id = ?",
            (uid, uid),
        )
        db.execute("DELETE FROM feedback WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM admin_notes WHERE admin_id = ?", (uid,))
        db.execute(
            "DELETE FROM media_hash_reports WHERE reporter_id = ? OR uploader_id = ?",
            (uid, uid),
        )
        db.execute("DELETE FROM media_blocked_hashes WHERE blocked_by = ?", (uid,))
        db.execute(
            "DELETE FROM media_blocked_users WHERE user_id = ? OR blocked_by = ?",
            (uid, uid),
        )

        db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_audit_log WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_bots WHERE owner_id = ?", (uid,))
        db.execute("DELETE FROM auth_devices WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_known_ips WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_email_tokens WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_2fa_challenges WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM user_features WHERE user_id = ?", (uid,))

        db.execute("DELETE FROM pres_presence WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM pres_custom_status WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM pres_activity WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM pres_typing WHERE user_id = ?", (uid,))
        db.execute(
            "DELETE FROM rel_friends WHERE user_id = ? OR friend_id = ?", (uid, uid)
        )
        db.execute(
            "DELETE FROM rel_blocked WHERE blocker_id = ? OR blocked_id = ?", (uid, uid)
        )
        db.execute(
            "DELETE FROM rel_friend_requests WHERE sender_id = ? OR recipient_id = ?",
            (uid, uid),
        )

        db.execute("DELETE FROM msg_user_settings WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM msg_content_filters WHERE user_id = ?", (uid,))

        db.execute("DELETE FROM media_files WHERE uploaded_by = ?", (uid,))
        try:
            db.execute("DELETE FROM media_upload_sessions WHERE user_id = ?", (uid,))
        except Exception:
            pass

        try:
            db.execute(
                "DELETE FROM admin_role_assignments WHERE admin_id = ? OR assigned_by = ?",
                (uid, uid),
            )
        except Exception:
            pass
        try:
            db.execute("DELETE FROM admin_users WHERE id = ?", (uid,))
        except Exception:
            pass
        try:
            db.execute("DELETE FROM admin_approvals WHERE requested_by = ?", (uid,))
        except Exception:
            pass
        try:
            if db.column_exists("admin_audit_log", "target_user_id"):
                db.execute(
                    "DELETE FROM admin_audit_log WHERE admin_id = ? OR target_user_id = ?",
                    (uid, uid),
                )
            else:
                db.execute(
                    "DELETE FROM admin_audit_log WHERE admin_id = ?",
                    (uid,),
                )
        except Exception:
            pass

        srv_rows = db.fetch_all("SELECT id FROM srv_servers WHERE owner_id = ?", (uid,))
        for s_row in srv_rows:
            sid = s_row["id"] if isinstance(s_row, dict) else s_row[0]
            self.delete_server_recursive(db, sid)

        db.execute(
            "DELETE FROM srv_member_roles WHERE member_id IN (SELECT id FROM srv_members WHERE user_id = ?)",
            (uid,),
        )
        db.execute("DELETE FROM srv_members WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM srv_onboarding_progress WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM srv_event_rsvps WHERE user_id = ?", (uid,))

        db.execute("DELETE FROM auth_users WHERE id = ?", (uid,))

    def delete_server_recursive(self, db: Any, sid: int) -> None:
        db.execute(
            "DELETE FROM srv_member_roles WHERE member_id IN (SELECT id FROM srv_members WHERE server_id = ?)",
            (sid,),
        )
        db.execute("DELETE FROM srv_members WHERE server_id = ?", (sid,))
        db.execute(
            "DELETE FROM srv_channel_overrides WHERE channel_id IN (SELECT id FROM srv_channels WHERE server_id = ?)",
            (sid,),
        )
        db.execute("DELETE FROM srv_invites WHERE server_id = ?", (sid,))
        db.execute("DELETE FROM srv_bans WHERE server_id = ?", (sid,))
        db.execute("DELETE FROM srv_categories WHERE server_id = ?", (sid,))
        db.execute("DELETE FROM srv_audit_log WHERE server_id = ?", (sid,))
        db.execute("DELETE FROM srv_scheduled_events WHERE server_id = ?", (sid,))
        db.execute("DELETE FROM srv_templates WHERE source_server_id = ?", (sid,))
        db.execute("DELETE FROM srv_welcome_screens WHERE server_id = ?", (sid,))
        db.execute("DELETE FROM srv_onboarding_steps WHERE server_id = ?", (sid,))
        db.execute("DELETE FROM srv_onboarding_progress WHERE server_id = ?", (sid,))

        conv_ids = db.fetch_all(
            "SELECT conversation_id FROM srv_channels WHERE server_id = ? AND conversation_id IS NOT NULL",
            (sid,),
        )
        for row in conv_ids:
            cid = row["conversation_id"] if isinstance(row, dict) else row[0]
            db.execute(
                "DELETE FROM msg_message_status WHERE message_id IN (SELECT id FROM msg_messages WHERE conversation_id = ?)",
                (cid,),
            )
            db.execute("DELETE FROM msg_pinned WHERE conversation_id = ?", (cid,))
            db.execute(
                "DELETE FROM msg_attachments WHERE message_id IN (SELECT id FROM msg_messages WHERE conversation_id = ?)",
                (cid,),
            )
            db.execute("DELETE FROM msg_messages WHERE conversation_id = ?", (cid,))
            db.execute("DELETE FROM msg_participants WHERE conversation_id = ?", (cid,))

            try:
                db.execute(
                    "DELETE FROM thread_members WHERE thread_id IN (SELECT id FROM thread_threads WHERE conversation_id = ?)",
                    (cid,),
                )
                db.execute(
                    "DELETE FROM thread_threads WHERE conversation_id = ?", (cid,)
                )
            except Exception:
                pass

            db.execute("DELETE FROM msg_conversations WHERE id = ?", (cid,))

        db.execute("DELETE FROM srv_channels WHERE server_id = ?", (sid,))
        db.execute("DELETE FROM srv_roles WHERE server_id = ?", (sid,))

        db.execute(
            "DELETE FROM webhook_messages WHERE webhook_id IN (SELECT id FROM webhook_webhooks WHERE server_id = ?)",
            (sid,),
        )
        db.execute("DELETE FROM webhook_webhooks WHERE server_id = ?", (sid,))

        db.execute("DELETE FROM srv_servers WHERE id = ?", (sid,))

    def cleanup_test_data(self) -> None:
        logger.info("Cleaning up test data via API...")

        api_success = self.api_cleanup()

        if not api_success or self.ctx.test_user_id:
            db = api.get_db()
            if not db:
                return

            try:
                if db.type == "sqlite":
                    db.execute("PRAGMA foreign_keys=OFF")

                db.begin_transaction()
                if self.ctx.test_user_id:
                    self.delete_all_for_user(db, self.ctx.test_user_id)
                if (
                    self.ctx.test_other_user_id
                    and self.ctx.test_other_user_id != self.ctx.test_user_id
                ):
                    self.delete_all_for_user(db, self.ctx.test_other_user_id)

                if db.in_transaction:
                    try:
                        db.commit()
                    except Exception as e:
                        logger.warning(f"Commit failed, attempting rollback: {e}")
                        try:
                            db.rollback()
                        except Exception:
                            pass
                else:
                    logger.debug("No active transaction to commit")
                logger.debug("SQL fallback cleanup completed")
            except Exception as e:
                db.rollback()
                logger.error(f"SQL fallback cleanup failed: {e}")
            finally:
                if db.type == "sqlite":
                    db.execute("PRAGMA foreign_keys=ON")

    def api_cleanup(self) -> bool:
        resources = []

        if self.ctx.test_poll_id:
            try:
                close_resp = self.ctx.session.post(
                    f"{self.ctx.base_url}/api/v1/polls/{self.ctx.test_poll_id}/close",
                    timeout=5,
                )
                logger.debug(f"API cleanup poll close: {close_resp.status_code}")
            except Exception as e:
                logger.debug(f"API cleanup poll close exception: {e}")

        if self.ctx.test_message_id and self.ctx.test_conversation_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/channels/{self.ctx.test_channel_id}/messages/{self.ctx.test_message_id}",
                )
            )

        if self.ctx.test_webhook_id:
            resources.append(("DELETE", f"/api/v1/webhooks/{self.ctx.test_webhook_id}"))

        if self.ctx.test_emoji_id and self.ctx.test_server_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/servers/{self.ctx.test_server_id}/emojis/{self.ctx.test_emoji_id}",
                )
            )

        if self.ctx.test_sticker_id:
            resources.append(("DELETE", f"/api/v1/stickers/{self.ctx.test_sticker_id}"))

        if self.ctx.test_poll_id:
            resources.append(("DELETE", f"/api/v1/polls/{self.ctx.test_poll_id}"))

        if self.ctx.test_automod_rule_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/automod/rules/{self.ctx.test_automod_rule_id}",
                )
            )

        if self.ctx.test_access_token_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/admin/access-tokens/{self.ctx.test_access_token_id}",
                )
            )

        if self.ctx.test_thread_id:
            resources.append(("DELETE", f"/api/v1/threads/{self.ctx.test_thread_id}"))

        if self.ctx.test_channel_id:
            resources.append(("DELETE", f"/api/v1/channels/{self.ctx.test_channel_id}"))

        if self.ctx.test_invite_code:
            resources.append(
                ("DELETE", f"/api/v1/channels/invites/{self.ctx.test_invite_code}")
            )

        if self.ctx.test_role_id and self.ctx.test_server_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/servers/{self.ctx.test_server_id}/roles/{self.ctx.test_role_id}",
                )
            )

        if self.ctx.test_server_id:
            resources.append(("DELETE", f"/api/v1/servers/{self.ctx.test_server_id}"))

        if self.ctx.test_application_id:
            resources.append(
                ("DELETE", f"/api/v1/applications/{self.ctx.test_application_id}")
            )

        if self.ctx.test_friend_request_id:
            try:
                fr_resp = self.ctx.session.post(
                    f"{self.ctx.base_url}/api/v1/relationships/{self.ctx.test_friend_request_id}/cancel",
                    timeout=5,
                )
                logger.debug(
                    f"API cleanup friend request cancel: {fr_resp.status_code}"
                )
            except Exception as e:
                logger.debug(f"API cleanup friend request exception: {e}")

        if self.ctx.test_report_id:
            try:
                rpt_resp = self.ctx.session.patch(
                    f"{self.ctx.base_url}/api/v1/reports/users/{self.ctx.test_report_id}",
                    json={"status": "resolved"},
                    timeout=5,
                )
                logger.debug(f"API cleanup user report resolve: {rpt_resp.status_code}")
            except Exception as e:
                logger.debug(f"API cleanup user report exception: {e}")

        all_ok = True
        for method, path in resources:
            try:
                resp = self.ctx.session.request(
                    method, f"{self.ctx.base_url}{path}", timeout=5
                )
                if 200 <= resp.status_code < 300:
                    logger.debug(
                        f"API cleanup OK: {method} {path} -> {resp.status_code}"
                    )
                elif resp.status_code == 404:
                    logger.debug(
                        f"API cleanup skipped (already deleted): {method} {path} -> {resp.status_code}"
                    )
                else:
                    logger.warning(
                        f"API cleanup failed: {method} {path} -> {resp.status_code}"
                    )
                    all_ok = False
            except Exception as e:
                logger.warning(f"API cleanup exception: {method} {path} -> {e}")
                all_ok = False

        return all_ok
