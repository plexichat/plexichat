"""
Cleanup service for SelfTestRunner.

NON-DESTRUCTIVE BY DESIGN: the self-test is a routine status check that
may run against a live deployment, so it may only ever remove rows it
provably owns (accounts named ``selftest_%`` and resources created by
this run and recorded in the context). Whole-table DELETEs, truncation,
and migration rollbacks against shared data are categorically forbidden
here — cleanup is scoped per selftest user and per run-tracked resource.
"""

from typing import Any

import src.api as api
import utils.logger as logger

from ..context import SelfTestContext
from ..db_guard import sanitize_error


class CleanupService:
    """Manages test data cleanup (API calls + SQL fallback)."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def pre_test_cleanup(self) -> None:
        """Remove leftovers from previous selftest runs. NEVER touches shared data.

        Only rows owned by ``selftest_%`` accounts (and their selftest-owned
        servers) are deleted. There are deliberately NO whole-table DELETEs:
        the self-test must be safe to run against a live deployment at any
        time.
        """
        logger.info("Performing pre-test cleanup (selftest-owned rows only)...")
        db = api.get_db()
        if not db:
            return

        try:
            if db.type == "sqlite":
                db.execute("PRAGMA foreign_keys=OFF")

            # Reconcile privileged artifacts left behind by a previously
            # crashed run (wildcard grants, elevated accounts) BEFORE any
            # other work, then clear the journal. The journal's SQL is
            # scoped to selftest_% accounts, so live users are untouched.
            journal = getattr(self.ctx, "journal", None)
            if journal is not None:
                try:
                    reconciled = journal.reconcile(db)
                    if reconciled:
                        logger.info(
                            "Journal reconciliation revoked %s recorded "
                            "privileged artifacts from a previous run",
                            reconciled,
                        )
                except Exception as e:
                    logger.warning(
                        "Journal reconciliation failed (non-critical): %s",
                        sanitize_error(e),
                    )

            rows = db.fetch_all(
                "SELECT id, username FROM auth_users WHERE username LIKE 'selftest_%'"
            )
            for row in rows:
                uid = row["id"] if isinstance(row, dict) else row[0]
                self._purge_sum_for_user(db, uid)

            self.sweep_orphans(db)

            db.begin_transaction()

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
            # NOTE: no whole-table DELETEs here. The self-test runs on live
            # deployments; only selftest_%-owned rows may ever be removed.

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
                self.delete_all_for_user(db, uid, purge_sum=False)
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

    def _purge_sum_for_user(self, db: Any, uid: int) -> None:
        """Best-effort Sum blob purge before entering a caller-owned SQL transaction.

        Never raises. A leftover workbook whose blob is unreadable (for
        example storage from a previous run lived in an ephemeral temp dir)
        must not abort the whole self-test cleanup: the SQL fallback in
        ``delete_all_for_user`` removes the metadata rows instead, so the
        next run starts clean.
        """
        try:
            from src.core.suite import sum as sum_domain

            sum_domain.get_manager().purge_user_data(uid)
        except Exception as e:
            logger.warning(f"Sum blob purge skipped for user {uid}: {e}")

    def delete_all_for_user(self, db: Any, uid: int, purge_sum: bool = True) -> None:
        # PlexiSuite resources are user-owned metadata. Sum workbooks must be
        # purged through the domain manager first so external .plexisum blobs
        # and linked artifacts are not orphaned. The remaining SQL is only a
        # schema-tolerant fallback for installations without an initialized Sum
        # domain.
        if purge_sum:
            self._purge_sum_for_user(db, uid)
        for statement, params in (
            (
                "DELETE FROM sum_range_permissions WHERE workbook_id IN "
                "(SELECT id FROM sum_workbooks WHERE owner_id = ?)",
                (uid,),
            ),
            (
                "DELETE FROM sum_grants WHERE workbook_id IN "
                "(SELECT id FROM sum_workbooks WHERE owner_id = ?)",
                (uid,),
            ),
            # Metadata fallback for workbooks whose blobs could not be purged
            # (see _purge_sum_for_user): without this, rows pointing at
            # missing .plexisum files would make every future purge retry and
            # fail the same way.
            (
                "DELETE FROM sum_snapshots WHERE workbook_id IN "
                "(SELECT id FROM sum_workbooks WHERE owner_id = ?)",
                (uid,),
            ),
            (
                "DELETE FROM sum_revision_blobs WHERE workbook_id IN "
                "(SELECT id FROM sum_workbooks WHERE owner_id = ?)",
                (uid,),
            ),
            (
                "DELETE FROM sum_operations WHERE workbook_id IN "
                "(SELECT id FROM sum_workbooks WHERE owner_id = ?)",
                (uid,),
            ),
            (
                "DELETE FROM sum_features WHERE workbook_id IN "
                "(SELECT id FROM sum_workbooks WHERE owner_id = ?)",
                (uid,),
            ),
            (
                "DELETE FROM sum_workbooks WHERE owner_id = ?",
                (uid,),
            ),
            # Threads outlive their channel otherwise: remove memberships,
            # messages, and threads the user owns (fuzzer-created threads
            # would otherwise dangle after the user row is deleted).
            ("DELETE FROM thread_members WHERE user_id = ?", (uid,)),
            (
                "DELETE FROM thread_messages WHERE thread_id IN "
                "(SELECT id FROM thread_threads WHERE owner_id = ?)",
                (uid,),
            ),
            (
                "DELETE FROM thread_members WHERE thread_id IN "
                "(SELECT id FROM thread_threads WHERE owner_id = ?)",
                (uid,),
            ),
            (
                "DELETE FROM thread_threads WHERE owner_id = ?",
                (uid,),
            ),
            ("DELETE FROM suite_audit_log WHERE actor_id = ?", (uid,)),
            ("DELETE FROM stash_download_tickets WHERE subject_id = ?", (uid,)),
            (
                "DELETE FROM stash_grants WHERE created_by = ? OR (subject_type = 'user' AND subject_id = ?) OR item_id IN (SELECT id FROM stash_items WHERE owner_id = ?)",
                (uid, str(uid), uid),
            ),
            (
                "DELETE FROM stash_versions WHERE created_by = ? OR item_id IN (SELECT id FROM stash_items WHERE owner_id = ?)",
                (uid, uid),
            ),
            ("DELETE FROM stash_items WHERE owner_id = ?", (uid,)),
            ("DELETE FROM stash_collections WHERE owner_id = ?", (uid,)),
        ):
            try:
                db.execute(statement, params)
            except Exception as exc:
                logger.debug("Optional PlexiSuite cleanup skipped: %s", exc)

        db.execute(
            "DELETE FROM automod_violations WHERE user_id = ? "
            "OR server_id IN (SELECT id FROM srv_servers WHERE owner_id = ?)",
            (uid, uid),
        )
        db.execute(
            "DELETE FROM automod_audit WHERE target_user_id = ? "
            "OR server_id IN (SELECT id FROM srv_servers WHERE owner_id = ?)",
            (uid, uid),
        )
        db.execute(
            "DELETE FROM automod_reputation WHERE user_id = ? "
            "OR server_id IN (SELECT id FROM srv_servers WHERE owner_id = ?)",
            (uid, uid),
        )
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

        from src.core.erasure import purge_owned_servers, purge_user_media

        purge_owned_servers(db, uid)
        # Uploaded media would otherwise outlive the account it belongs to.
        purge_user_media(db, uid)

        db.execute(
            "DELETE FROM srv_member_roles WHERE member_id IN (SELECT id FROM srv_members WHERE user_id = ?)",
            (uid,),
        )
        db.execute("DELETE FROM srv_members WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM srv_onboarding_progress WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM srv_event_rsvps WHERE user_id = ?", (uid,))

        db.execute("DELETE FROM auth_users WHERE id = ?", (uid,))

    def delete_server_recursive(self, db: Any, sid: int) -> None:
        """Delete a server and everything hanging off it.

        Delegates to ``src.core.erasure`` so the self-test and the account
        reaper cannot drift: erasure of a server means the same rows in both.
        """
        from src.core.erasure import purge_server_recursive

        purge_server_recursive(db, sid)

    def sweep_orphans(self, db: Any) -> dict:
        """Remove residue left by account erasures that predate the cascade.

        Account deletion now erases the servers, participant-less
        conversations and uploaded media an account owned, but a deployment
        can still carry rows from an earlier release (and a crash mid-purge
        leaves a partial one). Only rows whose owning account no longer exists
        are touched, so live data is never in scope.
        """
        try:
            from src.core.erasure import sweep_orphans as _sweep

            result = _sweep(db)
        except Exception as exc:
            logger.warning("Pre-test cleanup: orphan sweep skipped: %s", exc)
            return {}
        if any(result.get(k) for k in ("servers", "conversations", "media_unswept")):
            logger.info(
                "Pre-test cleanup: swept %s orphan server(s), %s orphan "
                "conversation(s), %s unswept media object(s)",
                len(result.get("servers") or []),
                len(result.get("conversations") or []),
                len(result.get("media_unswept") or []),
            )
        return result

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

                # Sum owns external blobs and rejects nested lifecycle
                # transactions. Purge those files before opening the SQL
                # transaction used for the remaining user cleanup.
                if self.ctx.test_user_id:
                    self._purge_sum_for_user(db, self.ctx.test_user_id)
                if (
                    self.ctx.test_other_user_id
                    and self.ctx.test_other_user_id != self.ctx.test_user_id
                ):
                    self._purge_sum_for_user(db, self.ctx.test_other_user_id)

                db.begin_transaction()
                if self.ctx.test_user_id:
                    self.delete_all_for_user(db, self.ctx.test_user_id, purge_sum=False)
                if (
                    self.ctx.test_other_user_id
                    and self.ctx.test_other_user_id != self.ctx.test_user_id
                ):
                    self.delete_all_for_user(
                        db, self.ctx.test_other_user_id, purge_sum=False
                    )

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

        if self.ctx.test_automod_rule_id and self.ctx.test_server_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/servers/{self.ctx.test_server_id}/automod/rules/{self.ctx.test_automod_rule_id}",
                )
            )

        if self.ctx.test_access_token_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/admin/access-tokens/{self.ctx.test_access_token_id}",
                )
            )

        # No DELETE endpoint for threads in API; core has delete_thread() but not exposed
        # if self.ctx.test_thread_id:
        #     resources.append(("DELETE", f"/api/v1/threads/{self.ctx.test_thread_id}"))

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
                elif resp.status_code in (404, 403):
                    logger.debug(
                        f"API cleanup skipped (already deleted/no access): {method} {path} -> {resp.status_code}"
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
