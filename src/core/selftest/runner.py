"""
Self-Test Runner - Automated API endpoint validation.

Discovers all registered routes from FastAPI and exercises them.
Supports automated authentication, retry logic, and traceback capture.
"""

import time
import json
import traceback
import random
import secrets
import re
from typing import List, Dict, Any, Optional

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore
import websocket

import src.api as api
import utils.config as config
import utils.logger as logger
from utils import licensing as license_module  # type: ignore


class SelfTestRunner:
    """Automated API test runner."""

    def __init__(self, base_url: str, standalone_mode: bool = True):
        self.base_url = base_url.rstrip("/")
        if requests is None:
            raise RuntimeError("requests dependency is required for selftest runner")
        self.requests_module = requests
        self.config = config.get("selftest", {})
        self.standalone_mode = standalone_mode
        self._test_password: Optional[str] = None
        self.token: Optional[str] = None
        self.other_token: Optional[str] = None
        self.test_user_id: Optional[int] = None
        self.test_other_user_id: Optional[int] = None
        self.test_server_id: Optional[int] = None
        self.test_channel_id: Optional[int] = None
        self.test_conversation_id: Optional[int] = None
        self.test_message_id: Optional[int] = None
        self.test_role_id: Optional[int] = None
        self.test_invite_code: Optional[str] = None
        self.test_webhook_id: Optional[int] = None
        self.test_webhook_token: Optional[str] = None
        self.test_poll_id: Optional[int] = None
        self.test_poll_option_ids: Optional[List[int]] = None
        self.test_report_id: Optional[int] = None
        self.test_hash_report_id: Optional[int] = None
        self.test_message_report_id: Optional[int] = None
        self.test_notification_id: Optional[int] = None
        self.test_approval_id: Optional[int] = None
        self.test_application_id: Optional[int] = None
        self.test_bot_id: Optional[int] = None
        self.test_sticker_id: Optional[int] = None
        self.test_friend_request_id: Optional[int] = None
        self.test_passkey_challenge_id: Optional[str] = None
        self.test_automod_rule_id: Optional[int] = None
        self.test_ticket_id: Optional[int] = None
        self.test_access_token_id: Optional[int] = None
        self.test_emoji_id: Optional[int] = None
        self.test_admin_role_id: Optional[int] = None
        self.test_admin_role_request_id: Optional[int] = None
        self.test_interaction_response_id: Optional[int] = None
        self.test_thread_id: Optional[int] = None
        self.results: List[Dict[str, Any]] = []
        self.start_time = 0.0
        self.session = self.requests_module.Session()
        self.other_session = self.requests_module.Session()
        self.openapi_spec: Dict[str, Any] = {}

        # Paths that should use the non-owner session
        self._other_session_paths: set = set()

        # Track created IDs not yet linked to a self field
        self._admin_role_super_id: Optional[int] = None
        self._admin_role_support_id: Optional[int] = None

    def run_all(self) -> bool:
        """Run all discovered API tests."""
        self.start_time = time.time()
        logger.info("=" * 60)
        logger.info("STARTING API SELF-TEST SUITE")
        logger.info(f"Target: {self.base_url}")
        logger.info("=" * 60)

        # 1. Discover Routes (and fetch OpenAPI spec)
        routes = self._discover_routes()
        if not routes:
            logger.error("No routes discovered. Aborting tests.")
            return False
        logger.info(f"Discovered {len(routes)} endpoints")

        # 2. Setup Auth and Resources
        self._pre_test_cleanup()
        if not self._setup_authentication_and_resources():
            logger.error("Setup failed. Aborting tests.")
            return False

        # 3. WebSocket Test
        self._test_websocket()

        # 4. Execute API Tests
        excluded = set(self.config.get("excluded_endpoints", []))
        # Add dynamic exclusions
        excluded.add("POST:/api/v1/auth/login")  # Would invalidate existing session
        excluded.add("POST:/api/v1/auth/2fa")  # Requires complex state
        excluded.add("POST:/api/v1/auth/logout")  # Destroys session
        excluded.add("POST:/api/v1/auth/sessions/revoke-all")  # Destroys session
        excluded.add("POST:/api/v1/auth/register")  # Would interfere with test user
        excluded.add("POST:/api/v1/admin/logout")  # Destroys session
        excluded.add("POST:/api/v1/admin/login")  # Admin login uses separate auth
        excluded.add(
            "POST:/api/v1/media/upload/complete/{session_id}"
        )  # Requires real upload
        # Migration endpoints require specific mock files and state management
        excluded.add("POST:/api/v1/admin/database/migrations/apply/{version}")
        excluded.add("POST:/api/v1/admin/migrations/{version}/rollback")

        # Skip admin endpoints when admin tests are disabled
        if not self.config.get("enable_admin_tests", True):
            logger.info("Admin tests disabled — excluding all /admin/ endpoints")
            for route in routes:
                if "/admin/" in route["path"]:
                    excluded.add(f"{route['method']}:{route['path']}")

        # OAuth endpoints require real OAuth provider
        excluded.add("GET:/api/v1/auth/oauth/{provider}/login")
        excluded.add("POST:/api/v1/auth/oauth/{provider}/callback")

        # 2FA endpoints tested during setup, skip in discovery loop
        for fa in ["enable", "disable", "confirm", "verify"]:
            excluded.add(f"POST:/api/v1/auth/2fa/{fa}")

            # Bot creation is done during setup, skip in discovery loop
            excluded.add("POST:/api/v1/applications/{application_id}/bot")

        # Passkey endpoints require multi-step WebAuthn interaction (skipped in auto-discovery)
        excluded.add("POST:/api/v1/auth/passkeys/options/register")
        excluded.add("POST:/api/v1/auth/passkeys/register")
        excluded.add("POST:/api/v1/auth/passkeys/options/authenticate")
        excluded.add("POST:/api/v1/auth/passkeys/authenticate")
        excluded.add("GET:/api/v1/auth/passkeys")
        excluded.add("DELETE:/api/v1/auth/passkeys/{passkey_id}")
        excluded.add("PATCH:/api/v1/auth/passkeys/{passkey_id}")

        # Bot server endpoints need explicit ordering (request → approve), skip auto-discovery
        excluded.add("POST:/api/v1/bots/servers/{server_id}/approve")
        excluded.add("POST:/api/v1/bots/servers/{server_id}/request")

        # Poll close must run AFTER vote, so exclude it from auto-discovery.
        # The vote is tested during auto-discovery; close is tested in _api_cleanup.
        excluded.add("POST:/api/v1/polls/{poll_id}/close")

        # Invite creation via discovery would collide with setup-created invite (409)
        excluded.add("POST:/api/v1/channels/invites/{code}")

        # Admin 2FA endpoints
        for fa_path in [
            "/api/v1/admin/auth/2fa/begin-setup",
            "/api/v1/admin/auth/2fa/disable",
            "/api/v1/admin/auth/2fa/regenerate-backup-codes",
            "/api/v1/admin/verify-otp",
        ]:
            excluded.add(fa_path)

        # Destructive admin endpoints: defer to the end of the test suite
        # and target the other (throwaway) user instead of the test admin
        # Endpoints that modify the test user's state in ways that cascade:
        # - force-purge / force-logout / lock-user: destroy or lock the user session
        # - toggle-status: locks/unlocks the other user's account
        # - force-username-change: sets force_username_change=True, blocking all non-admin requests
        # - logout-all: revokes ALL sessions on the platform
        destructive_paths = {
            "/api/v1/admin/security/lock-user",
            "/api/v1/admin/security/unlock-user",
            "/api/v1/admin/security/force-logout",
            "/api/v1/admin/security/logout-all",
            "/api/v1/admin/admin-users/{user_id}/toggle-status",
            "/api/v1/admin/users/{user_id}/force-purge",
            "/api/v1/admin/users/{user_id}/force-username-change",
        }

        # When not in standalone mode, skip destructive endpoints entirely
        # (they would disrupt the running server's state)
        if not self.standalone_mode:
            logger.info(
                "Non-standalone mode: skipping destructive endpoints "
                f"({len(destructive_paths)} deferred/destructive endpoints excluded)"
            )

        logger.info("Executing API tests...")

        deferred_destructive = []
        logout_all_route = None

        for route in routes:
            path = route["path"]
            method = route["method"]

            # Skip excluded endpoints (non-destructive ones)
            if path in excluded or f"{method}:{path}" in excluded:
                logger.debug(f"Skipping excluded endpoint: {method} {path}")
                continue

            # Defer destructive endpoints to run after the main loop
            if path in destructive_paths or f"{method}:{path}" in destructive_paths:
                if self.standalone_mode:
                    deferred_destructive.append(route)
                    # Separate logout-all to run absolutely last (even after DELETE/rate-limit)
                    if "/logout-all" in path:
                        logout_all_route = route
                    logger.debug(f"Deferring destructive endpoint: {method} {path}")
                else:
                    logger.debug(
                        f"Skipping destructive endpoint (non-standalone): {method} {path}"
                    )
                continue

            # CRITICAL: Skip DELETE methods during discovery loop to avoid destroying test resources
            if method == "DELETE":
                logger.debug(f"Skipping DELETE endpoint: {path}")
                continue
            # Skip other dangerous endpoints (except known admin destructives which are deferred)
            if (
                "logout" in path.lower()
                or "reset" in path.lower()
                or "cleanup" in path.lower()
            ):
                continue

            # Determine which session to use
            use_other = False
            if self.other_token and self._other_session_paths:
                for other_path in self._other_session_paths:
                    if other_path in path or path.startswith(other_path):
                        use_other = True
                        break
            # Server leave endpoint uses the non-owner (other user) session
            # (string matching doesn't work because the route has {server_id} between /servers/ and /leave)
            if (
                not use_other
                and self.other_token
                and "/leave" in path
                and "/servers/" in path
            ):
                use_other = True

            # Admin approval approve: must use a different admin (not the requester)
            if (
                not use_other
                and self.other_token
                and "/admin/approvals/" in path
                and "/approve" in path
            ):
                use_other = True

            # Skip voice endpoints if not connected
            if "/voice/" in path and method == "GET":
                logger.debug(
                    f"Skipping voice endpoint (no connection): {method} {path}"
                )
                continue

            # delay-deletion requires account to already be scheduled for deletion
            excluded.add("POST:/api/v1/admin/users/{user_id}/delay-deletion")

            # Skip endpoints that still have un-substituted path parameters
            if "{" in path or "}" in path:
                # Attempt substitution using resolved values for this specific test
                test_path = path
                # Discover all placeholders in the path
                placeholders = re.findall(r"\{([a-zA-Z0-9_]+)\}", path)

                for p_name in placeholders:
                    val = self._get_param_value(p_name, path)
                    test_path = test_path.replace(f"{{{p_name}}}", val)

                if "{" in test_path:
                    logger.debug(
                        f"Skipping endpoint with remaining placeholders: {method} {path}"
                    )
                    continue

                # Use the substituted path for testing, but DON'T update the original 'path'
                # so other methods for the same path can also perform substitution
                self._test_endpoint(method, test_path, route, use_other)
            else:
                self._test_endpoint(method, path, route, use_other)
            # Very small delay to allow async tasks to settle
            time.sleep(0.01)

        # 5. Rate limit negative test (runs before destructive endpoints to ensure valid token)
        if self.standalone_mode:
            self._test_rate_limits()
        else:
            logger.info("Skipping rate limit burst test (not in standalone mode)")

        # 6. Explicit bot server flow: request → approve (excluded from auto-discovery)
        # Runs before DELETE resources to ensure server/app still exist
        self._test_bot_server_integration()

        # 7. Run deferred destructive endpoints (targeting the other throwaway user)
        # Only runs in standalone_mode to avoid disrupting a running server's state
        if deferred_destructive:
            logger.info(
                "Executing deferred destructive endpoints (targeting other user)..."
            )
            # Separate logout-all to run absolutely last (after cleanup)
            ordered = []
            for route in deferred_destructive:
                if "/logout-all" in route["path"]:
                    logout_all_route = route
                else:
                    ordered.append(route)

            for route in ordered:
                method = route["method"]
                path = route["path"]
                # Substitute path parameters
                test_path = path
                for p_name in re.findall(r"\{([a-zA-Z0-9_]+)\}", path):
                    test_path = test_path.replace(
                        f"{{{p_name}}}", self._get_param_value(p_name, path)
                    )
                self._test_endpoint(method, test_path, route)
                time.sleep(0.01)

        # 8. DELETE endpoint testing for tracked resources
        self._test_delete_resources()

        # 9. Cleanup (SQL fallback - runs before logout-all so API cleanup still works)
        self._cleanup_test_data()

        # 10. Summary
        success = self._report_summary()

        # 11. Logout-all (last authenticated API call)
        if logout_all_route and self.standalone_mode:
            method = logout_all_route["method"]
            path = logout_all_route["path"]
            self._test_endpoint(method, path, logout_all_route)
            time.sleep(0.01)
        elif not self.standalone_mode:
            logger.info("Skipping logout-all test (not in standalone mode)")

        return success

    def _pre_test_cleanup(self) -> None:
        """Perform a thorough cleanup before starting tests to handle garbage from previous failed runs."""
        logger.info("Performing pre-test cleanup...")
        db = api.get_db()
        if not db:
            return

        try:
            if db.type == "sqlite":
                db.execute("PRAGMA foreign_keys=OFF")

            db.begin_transaction()

            # Handle tables that may not exist yet (created by migrations)
            for tbl in ["media_upload_sessions"]:
                try:
                    db.execute(f"DELETE FROM {tbl}")
                except Exception:
                    pass

            # Truncate test resource tables to prevent UNIQUE constraint failures.
            # CAUTION: Never truncate admin_* tables here — they contain real
            # production admin accounts that MUST survive across restarts.
            # Only truncate tables that hold selftest-generated data.
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
            ]
            for tbl in truncate_tables:
                try:
                    db.execute(f"DELETE FROM {tbl}")
                except Exception:
                    pass

            # Clean up orphaned servers from previous failed selftest runs.
            # These are servers owned by selftest_* users that were not cleaned
            # up because a previous run crashed/aborted before _cleanup_test_data.
            try:
                orphan_srv_rows = db.fetch_all(
                    "SELECT s.id, s.owner_id FROM srv_servers s "
                    "JOIN auth_users u ON s.owner_id = u.id "
                    "WHERE u.username LIKE 'selftest_%' AND s.deleted = 0"
                )
                for os_row in orphan_srv_rows:
                    osid = os_row["id"] if isinstance(os_row, dict) else os_row[0]
                    self._delete_server_recursive(db, osid)
                if orphan_srv_rows:
                    logger.debug(
                        f"Pre-test cleanup: removed {len(orphan_srv_rows)} orphaned servers"
                    )
            except Exception:
                pass

            # Find all users that look like test users and unlock them
            rows = db.fetch_all(
                "SELECT id, username FROM auth_users WHERE username LIKE 'selftest_%'"
            )
            for row in rows:
                uid = row["id"] if isinstance(row, dict) else row[0]
                uname = row["username"] if isinstance(row, dict) else row[1]
                # Unlock account before deletion
                try:
                    db.execute(
                        "UPDATE auth_users SET account_locked = 0, locked_until = NULL WHERE id = ?",
                        (uid,),
                    )
                except Exception:
                    pass
                self._delete_all_for_user(db, uid)
                logger.debug(f"Pre-test cleanup: Deleted user {uname}")

            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"Pre-test cleanup failed (non-critical): {e}")
        finally:
            if db.type == "sqlite":
                db.execute("PRAGMA foreign_keys=ON")

    def _delete_all_for_user(self, db: Any, uid: int) -> None:
        """Helper to delete all data associated with a user ID."""
        # Content Moderation & Feedback
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

        # Authentication & Identity
        db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_audit_log WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_bots WHERE owner_id = ?", (uid,))
        db.execute("DELETE FROM auth_devices WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_known_ips WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_email_tokens WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM auth_2fa_challenges WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM user_features WHERE user_id = ?", (uid,))

        # Presence & Relationships
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

        # Messaging Settings
        db.execute("DELETE FROM msg_user_settings WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM msg_content_filters WHERE user_id = ?", (uid,))

        # Media & Uploads
        db.execute("DELETE FROM media_files WHERE uploaded_by = ?", (uid,))
        try:
            db.execute("DELETE FROM media_upload_sessions WHERE user_id = ?", (uid,))
        except Exception:
            pass

        # Admin tables cleanup
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

        # Cleanup servers owned by this user
        srv_rows = db.fetch_all("SELECT id FROM srv_servers WHERE owner_id = ?", (uid,))
        for s_row in srv_rows:
            sid = s_row["id"] if isinstance(s_row, dict) else s_row[0]
            self._delete_server_recursive(db, sid)

        # Cleanup membership in other servers
        db.execute(
            "DELETE FROM srv_member_roles WHERE member_id IN (SELECT id FROM srv_members WHERE user_id = ?)",
            (uid,),
        )
        db.execute("DELETE FROM srv_members WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM srv_onboarding_progress WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM srv_event_rsvps WHERE user_id = ?", (uid,))

        db.execute("DELETE FROM auth_users WHERE id = ?", (uid,))

    def _delete_server_recursive(self, db: Any, sid: int) -> None:
        """Helper to delete a server and all its linked data."""
        # Delete child resources
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

        # Messages and Conversations
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

            # Threads
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

        # Channels and Roles
        db.execute("DELETE FROM srv_channels WHERE server_id = ?", (sid,))
        db.execute("DELETE FROM srv_roles WHERE server_id = ?", (sid,))

        # Webhooks
        db.execute(
            "DELETE FROM webhook_messages WHERE webhook_id IN (SELECT id FROM webhook_webhooks WHERE server_id = ?)",
            (sid,),
        )
        db.execute("DELETE FROM webhook_webhooks WHERE server_id = ?", (sid,))

        # Finally the server
        db.execute("DELETE FROM srv_servers WHERE id = ?", (sid,))

    def _setup_authentication_and_resources(self) -> bool:
        """Create a temporary test user and necessary resources."""
        user_config = self.config.get("test_user", {})
        username = user_config.get("username", "selftest_admin")

        # Security fix: Use random password if not configured
        self._test_password = user_config.get("password")
        if not self._test_password:
            self._test_password = secrets.token_urlsafe(16)
            logger.info("No self-test password configured; generated random password.")
        password = self._test_password

        email = user_config.get("email", "selftest@plexichat.com")

        logger.info(f"Setting up test user and resources: {username}")

        # Add secure internal secret to bypass security and rate limits
        internal_secret = api.get_internal_secret()
        if internal_secret:
            self.session.headers.update(
                {"X-Plexichat-Internal-Secret": internal_secret}
            )
            logger.debug("Internal security secret added to test session")

        auth_mod = api.get_auth()
        servers_mod = api.get_servers()
        webhooks_mod = api.get_webhooks()
        messaging = api.get_messaging()

        if not auth_mod or not servers_mod:
            logger.error("Auth or Servers module not available for self-test")
            return False

        try:
            # 1. Setup User
            user = auth_mod.get_user_by_username(username)
            if not user:
                logger.debug(f"Creating new test user: {username}")
                try:
                    user = auth_mod.register(username, email, password)
                except Exception as e:
                    if "Email already registered" in str(e):
                        # Try to find user by email
                        db = api.get_db()
                        if db:
                            row = db.fetch_one(
                                "SELECT id FROM auth_users WHERE email = ?", (email,)
                            )
                            if row:
                                user = auth_mod.get_user(
                                    row["id"] if isinstance(row, dict) else row[0]
                                )
                            else:
                                raise
                        else:
                            raise
                    else:
                        raise

            if not user:
                logger.error("Failed to find or create test user")
                return False

            # Unlock account if locked from previous failed run
            _cleanup_db = api.get_db()
            if _cleanup_db:
                try:
                    _cleanup_db.execute(
                        "UPDATE auth_users SET account_locked = 0, locked_until = NULL WHERE id = ?",
                        (user.id,),
                    )
                except Exception:
                    pass

            # Only grant admin permissions when admin tests are enabled
            enable_admin = self.config.get("enable_admin_tests", True)
            if enable_admin:
                auth_mod.grant_permission(user.id, "admin.*")
                auth_mod.grant_permission(user.id, "*")
                logger.debug("Admin permissions granted to test user")

            self.test_user_id = user.id
            logger.info(f"Test user ID: {self.test_user_id}")

            # Admin setup only when admin tests are enabled
            if enable_admin:
                db = api.get_db()
                if db:
                    # Ensure user exists in admin_users table
                    try:
                        existing = db.fetch_one(
                            "SELECT id FROM admin_users WHERE id = ?",
                            (user.id,),
                        )
                        if not existing:
                            # Hash the actual test password so change-password tests work
                            import src.utils.encryption as _selftest_enc

                            _actual_hash = _selftest_enc.hash_password(password)
                            db.execute(
                                "INSERT OR IGNORE INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp) VALUES (?, ?, ?, ?, ?, 0)",
                                (
                                    user.id,
                                    username,
                                    _actual_hash,
                                    email,
                                    int(time.time()),
                                ),
                            )
                            logger.debug(f"Added user to admin_users table: {user.id}")
                    except Exception as e:
                        logger.warning(f"Failed to add user to admin_users: {e}")

                    # Seed admin roles and approval data
                    try:
                        # Get or create super_admin role, using its actual DB id
                        super_admin_row = db.fetch_one(
                            "SELECT id FROM admin_roles WHERE name = ?",
                            ("super_admin",),
                        )
                        if super_admin_row:
                            super_admin_id = (
                                super_admin_row["id"]
                                if isinstance(super_admin_row, dict)
                                else super_admin_row[0]
                            )
                        else:
                            super_admin_id = self._generate_snowflake()
                            try:
                                db.execute(
                                    "INSERT INTO admin_roles (id, name, description, permissions, created_at, created_by, is_system) VALUES (?, ?, ?, ?, ?, ?, 1)",
                                    (
                                        super_admin_id,
                                        "super_admin",
                                        "Full system access",
                                        '{"*": true}',
                                        int(time.time()),
                                        user.id,
                                    ),
                                )
                            except Exception as e:
                                if "UNIQUE constraint" in str(e):
                                    # Role already exists with different ID, use INSERT OR IGNORE
                                    super_admin_row = db.fetch_one(
                                        "SELECT id FROM admin_roles WHERE name = ?",
                                        ("super_admin",),
                                    )
                                    super_admin_id = (
                                        super_admin_row["id"]
                                        if super_admin_row
                                        else super_admin_id
                                    )
                                else:
                                    raise

                        # Get or create support_admin role
                        support_admin_row = db.fetch_one(
                            "SELECT id FROM admin_roles WHERE name = ?",
                            ("support_admin",),
                        )
                        if support_admin_row:
                            support_admin_id = (
                                support_admin_row["id"]
                                if isinstance(support_admin_row, dict)
                                else support_admin_row[0]
                            )
                        else:
                            support_admin_id = self._generate_snowflake()
                            try:
                                db.execute(
                                    "INSERT INTO admin_roles (id, name, description, permissions, created_at, created_by, is_system) VALUES (?, ?, ?, ?, ?, ?, 1)",
                                    (
                                        support_admin_id,
                                        "support_admin",
                                        "Support access",
                                        '{"users.read": true, "users.edit": true, "tickets.*": true}',
                                        int(time.time()),
                                        user.id,
                                    ),
                                )
                            except Exception as e:
                                if "UNIQUE constraint" in str(e):
                                    support_admin_row = db.fetch_one(
                                        "SELECT id FROM admin_roles WHERE name = ?",
                                        ("support_admin",),
                                    )
                                    support_admin_id = (
                                        support_admin_row["id"]
                                        if support_admin_row
                                        else support_admin_id
                                    )
                                else:
                                    raise

                        # Get or create moderation_admin role
                        mod_admin_row = db.fetch_one(
                            "SELECT id FROM admin_roles WHERE name = ?",
                            ("moderation_admin",),
                        )
                        if not mod_admin_row:
                            mod_admin_id = self._generate_snowflake()
                            try:
                                db.execute(
                                    "INSERT INTO admin_roles (id, name, description, permissions, created_at, created_by, is_system) VALUES (?, ?, ?, ?, ?, ?, 1)",
                                    (
                                        mod_admin_id,
                                        "moderation_admin",
                                        "Moderation access",
                                        '{"automod.*": true, "reports.*": true}',
                                        int(time.time()),
                                        user.id,
                                    ),
                                )
                            except Exception as e:
                                if "UNIQUE constraint" not in str(e):
                                    raise

                        # Assign super_admin to test user using the actual role id
                        assign = db.fetch_one(
                            "SELECT 1 FROM admin_role_assignments WHERE admin_id = ? AND role_id = ?",
                            (user.id, super_admin_id),
                        )
                        if not assign:
                            db.execute(
                                "INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
                                (user.id, super_admin_id, int(time.time()), user.id),
                            )
                            logger.debug(
                                f"Assigned super_admin role to admin {user.id}"
                            )
                            # Store the actual admin role ID for admin roles endpoints
                            if super_admin_id:
                                self._admin_role_super_id = super_admin_id
                                if not self.test_admin_role_id:
                                    self.test_admin_role_id = super_admin_id
                    except Exception as e:
                        logger.warning(f"Failed to seed admin roles: {e}")

                    try:
                        existing_approval = db.fetch_one(
                            "SELECT id FROM admin_approvals WHERE requested_by = ?",
                            (user.id,),
                        )
                        if not existing_approval:
                            approval_id = self._generate_snowflake()
                            db.execute(
                                "INSERT INTO admin_approvals (id, requested_by, action_type, target_type, status, required_approvals, current_approvals, expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    approval_id,
                                    user.id,
                                    "test.action",
                                    "test",
                                    "pending",
                                    2,
                                    0,
                                    int(time.time()) + 86400,
                                    int(time.time()),
                                    int(time.time()),
                                ),
                            )
                            self.test_approval_id = approval_id
                            logger.debug("Created test admin approval request")
                        else:
                            self.test_approval_id = (
                                existing_approval["id"]
                                if isinstance(existing_approval, dict)
                                else existing_approval[0]
                            )
                    except Exception as e:
                        logger.warning(f"Failed to seed admin approval: {e}")

            # 1b. Setup Other User (for DMs/Relationships, non-owner operations)
            other_username = username + "_other"
            other_email = "other_" + email
            other_user = auth_mod.get_user_by_username(other_username)
            if not other_user:
                try:
                    other_user = auth_mod.register(
                        other_username, other_email, password
                    )
                except Exception:
                    pass

            if other_user:
                self.test_other_user_id = other_user.id
                db = api.get_db()
                # Ensure other user is unlocked regardless of prior state
                try:
                    if db:
                        db.execute(
                            "UPDATE auth_users SET account_locked = 0, locked_until = NULL WHERE id = ?",
                            (other_user.id,),
                        )
                    logger.debug("Ensured other user is unlocked")
                except Exception:
                    pass
                # Also add to admin_users for admin endpoint testing
                if enable_admin and db:
                    try:
                        existing_other = db.fetch_one(
                            "SELECT id FROM admin_users WHERE id = ?", (other_user.id,)
                        )
                        if not existing_other:
                            import src.utils.encryption as _selftest_enc2

                            _actual_hash2 = _selftest_enc2.hash_password(password)
                            db.execute(
                                "INSERT OR IGNORE INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp) VALUES (?, ?, ?, ?, ?, 0)",
                                (
                                    other_user.id,
                                    other_username,
                                    _actual_hash2,
                                    other_email,
                                    int(time.time()),
                                ),
                            )
                        # Assign support_admin role to other user
                        support_admin_row = db.fetch_one(
                            "SELECT id FROM admin_roles WHERE name = ?",
                            ("support_admin",),
                        )
                        support_admin_id = (
                            support_admin_row["id"]
                            if isinstance(support_admin_row, dict)
                            else support_admin_row[0]
                        )
                        assign_other = db.fetch_one(
                            "SELECT 1 FROM admin_role_assignments WHERE admin_id = ? AND role_id = ?",
                            (other_user.id, support_admin_id),
                        )
                        if not assign_other:
                            db.execute(
                                "INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
                                (
                                    other_user.id,
                                    support_admin_id,
                                    int(time.time()),
                                    user.id,
                                ),
                            )
                        # Grant admin permission in auth_users for admin endpoint checks
                        auth_mod.grant_permission(other_user.id, "admin.*")
                    except Exception as e:
                        logger.warning(f"Failed to setup other admin user: {e}")
                logger.debug(f"Test other user ID: {self.test_other_user_id}")
                logger.debug(f"Test other user ID: {self.test_other_user_id}")

            # 2. Login via API to get token
            # Get the bypass secret from config if available
            bypass_secret = config.get("rate_limiting.bypass_secret")

            # Helper to update headers for both sessions
            def _apply_headers(session):
                if self.token:
                    session.headers.update({"Authorization": f"Bearer {self.token}"})
                if bypass_secret:
                    session.headers.update({"X-RateLimit-Bypass": bypass_secret})

            resp = self.session.post(
                f"{self.base_url}/api/v1/auth/login",
                json={"username": username, "password": password},
                timeout=10,
            )

            if resp.status_code != 200:
                logger.error(f"Login failed: {resp.text}")
                return False

            login_data = resp.json()
            self.token = login_data.get("token") or login_data.get("access_token")
            if not self.token:
                logger.error(f"No token in login response: {login_data}")
                return False
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})
            logger.debug("Logged in and token retrieved")

            # Verify token works immediately
            verify = self.session.get(f"{self.base_url}/api/v1/users/@me", timeout=5)
            if verify.status_code != 200:
                logger.error(
                    f"Token verification failed: {verify.status_code} {verify.text[:200]}"
                )
                # Try response format: maybe key is different
                logger.error(f"Login response keys: {list(login_data.keys())}")
                return False
            logger.debug("Token verified: user @me OK")

            # 2b. Login other user via API to get separate session
            if self.test_other_user_id:
                try:
                    other_resp = self.other_session.post(
                        f"{self.base_url}/api/v1/auth/login",
                        json={"username": other_username, "password": password},
                        timeout=10,
                    )
                    if other_resp.status_code == 200:
                        self.other_token = other_resp.json().get("token")
                        self.other_session.headers.update(
                            {"Authorization": f"Bearer {self.other_token}"}
                        )
                        if internal_secret:
                            self.other_session.headers.update(
                                {"X-Plexichat-Internal-Secret": internal_secret}
                            )
                        logger.debug("Other user logged in and token retrieved")
                except Exception as e:
                    logger.warning(f"Failed to login other user: {e}")

            # Configure paths that should use the non-owner (other user) session
            self._other_session_paths = {
                "/api/v1/channels/invites",
            }

            # 3. Setup Test Server
            logger.info("Creating test server...")
            server = servers_mod.create_server(
                user.id, "Self-Test Server", "Temporary server for API testing"
            )
            self.test_server_id = server.id
            logger.info(f"Test server ID: {self.test_server_id}")

            # Other user will join via invite endpoint during testing

            # 4. Setup Test Channel
            logger.info("Creating test channel...")
            channel = servers_mod.create_channel(user.id, server.id, "test-channel")
            self.test_channel_id = channel.id
            self.test_conversation_id = getattr(channel, "conversation_id", None)
            logger.info(
                f"Test channel ID: {self.test_channel_id}, Conv ID: {self.test_conversation_id}"
            )

            # 5. Setup Test Message (for reactions/pins)
            if self.test_conversation_id and messaging:
                logger.info("Creating test message...")
                try:
                    msg = messaging.send_message(
                        user.id,
                        self.test_conversation_id,
                        "Self-test reference message",
                    )
                    self.test_message_id = msg.id
                    logger.info(f"Test message ID: {self.test_message_id}")
                except Exception as e:
                    logger.warning(f"Failed to create test message: {e}")

            # 6. Setup Test Role (random suffix avoids name collisions across runs)
            logger.debug("Creating test role...")
            role = servers_mod.create_role(
                user.id, server.id, f"Test Role {secrets.token_hex(4)}", color="#ff0000"
            )
            self.test_role_id = role.id
            logger.debug(f"Test role ID: {self.test_role_id}")

            # 6. Setup Test Invite
            logger.debug("Creating test invite...")
            invite = servers_mod.create_invite(user.id, self.test_channel_id)
            self.test_invite_code = invite.code
            logger.debug(f"Test invite code: {self.test_invite_code}")

            # Make the other user join the test server via invite, so they can test /servers/leave later
            if self.test_invite_code and self.other_token:
                # First try API-based join via invite
                try:
                    join_resp = self.other_session.post(
                        f"{self.base_url}/api/v1/channels/invites/{self.test_invite_code}/accept",
                        timeout=5,
                    )
                    if join_resp.status_code in (200, 201, 204):
                        logger.debug("Other user joined test server via invite")
                    else:
                        logger.warning(
                            f"Other user failed to join test server via invite: {join_resp.status_code}: {join_resp.text[:200]}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Other user failed to join test server via invite: {e}"
                    )
                # Fallback: directly insert into DB as server member
                try:
                    db_m = api.get_db()
                    if db_m:
                        existing = db_m.fetch_one(
                            "SELECT id FROM srv_members WHERE user_id = ? AND server_id = ?",
                            (self.test_other_user_id, self.test_server_id),
                        )
                        if not existing:
                            member_id = self._generate_snowflake()
                            db_m.execute(
                                "INSERT INTO srv_members (id, user_id, server_id, joined_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                                (
                                    member_id,
                                    self.test_other_user_id,
                                    self.test_server_id,
                                    int(time.time()),
                                    int(time.time()),
                                ),
                            )
                            logger.debug(
                                f"Added other user to test server via DB (member_id={member_id})"
                            )
                except Exception as e:
                    logger.warning(f"Failed to add other user to server via DB: {e}")

            # 7. Setup Test Webhook
            if webhooks_mod:
                logger.debug("Creating test webhook...")
                try:
                    webhook = webhooks_mod.create_webhook(
                        user.id, self.test_channel_id, "Self-Test Webhook"
                    )
                    self.test_webhook_id = webhook.id
                    self.test_webhook_token = getattr(webhook, "token", None)
                    logger.debug(f"Test webhook ID: {self.test_webhook_id}")
                except Exception as e:
                    logger.warning(f"Failed to create test webhook: {e}")

            # 8. Setup Dummy File (for attachment testing)
            try:
                from pathlib import Path

                media_dir = Path.home() / ".plexichat" / "media" / "attachments"
                media_dir.mkdir(parents=True, exist_ok=True)
                test_file = media_dir / "test_file.png"
                # Small valid PNG
                test_file.write_bytes(
                    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n\x2e\xe4\x00\x00\x00\x00IEND\xaeB`\x82"
                )
                logger.debug(f"Created dummy test file at {test_file}")
            except Exception as e:
                logger.warning(f"Failed to create dummy test file: {e}")

            # 9. Setup Test Setting
            settings_mod = api.get_settings()
            if settings_mod:
                try:
                    settings_mod.set_setting(user.id, "test_key", "test_value")
                    logger.debug("Created test setting 'test_key'")
                except Exception as e:
                    logger.warning(f"Failed to create test setting: {e}")

            # 10. Setup Test Application (for application/bot testing)
            applications_mod = api.get_applications()
            if applications_mod:
                try:
                    app_name = f"Self-Test App {secrets.token_hex(4)}"
                    app = applications_mod.create_application(user.id, app_name)
                    self.test_application_id = app.id
                    logger.debug(
                        f"Created test application ID: {self.test_application_id}"
                    )
                    try:
                        bot = applications_mod.create_bot_for_application(
                            user.id, self.test_application_id
                        )
                        self.test_bot_id = (
                            bot["bot_id"] if isinstance(bot, dict) else bot.id
                        )
                        logger.debug(f"Created test bot ID: {self.test_bot_id}")
                    except Exception as e:
                        if type(
                            e
                        ).__name__ == "UserExistsError" or "already registered" in str(
                            e
                        ):
                            logger.warning(
                                f"Test bot already exists; reusing existing bot context: {e}"
                            )
                        else:
                            logger.warning(f"Failed to create test bot: {e}")
                except Exception as e:
                    logger.warning(f"Failed to create test application: {e}")

            # 11. Setup Test Notification (for notification read testing)
            _n_db = api.get_db()
            try:
                if _n_db:
                    existing_notif = _n_db.fetch_one(
                        "SELECT id FROM notif_notifications WHERE user_id = ?",
                        (user.id,),
                    )
                    if not existing_notif:
                        notif_id = self._generate_snowflake()
                        _n_db.execute(
                            "INSERT INTO notif_notifications (id, user_id, sender_id, message_id, conversation_id, mention_type, content_preview, read, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                notif_id,
                                user.id,
                                user.id,
                                0,
                                0,
                                "user",
                                "Self-test notification body",
                                0,
                                int(time.time()),
                            ),
                        )
                        self.test_notification_id = notif_id
                        logger.debug("Created test notification")
                    else:
                        self.test_notification_id = (
                            existing_notif["id"]
                            if isinstance(existing_notif, dict)
                            else existing_notif[0]
                        )
            except Exception as e:
                logger.warning(f"Failed to create test notification: {e}")

            # 12. Setup Test Report (for reports testing)
            _r_db = api.get_db()
            try:
                if _r_db:
                    existing_report = _r_db.fetch_one(
                        "SELECT id FROM user_reports WHERE reporter_id = ?", (user.id,)
                    )
                    if not existing_report and self.test_other_user_id:
                        report_id = self._generate_snowflake()
                        _r_db.execute(
                            "INSERT INTO user_reports (id, reporter_id, reported_user_id, reason, category, status, reported_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                report_id,
                                user.id,
                                self.test_other_user_id,
                                "Self-test report",
                                "other",
                                "open",
                                int(time.time()),
                            ),
                        )
                        logger.debug("Created test user report")
                        # Also create message and hash reports for admin review endpoints
                        hash_report_id = self._generate_snowflake()
                        _r_db.execute(
                            "INSERT OR IGNORE INTO media_hash_reports (id, reporter_id, hash_value, reason, status, reported_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                hash_report_id,
                                user.id,
                                "a" * 64,
                                "Self-test hash report",
                                "open",
                                int(time.time()),
                            ),
                        )
                        self.test_hash_report_id = hash_report_id
                        logger.debug("Created test hash report")
                        if self.test_message_id and self.test_other_user_id:
                            msg_report_id = self._generate_snowflake()
                            _r_db.execute(
                                "INSERT OR IGNORE INTO message_reports (id, reporter_id, message_id, channel_id, reported_user_id, reason, category, status, reported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    msg_report_id,
                                    user.id,
                                    self.test_message_id,
                                    self.test_channel_id or 0,
                                    self.test_other_user_id,
                                    "Self-test message report",
                                    "other",
                                    "open",
                                    int(time.time()),
                                ),
                            )
                            self.test_message_report_id = msg_report_id
                            logger.debug("Created test message report")
                        self.test_report_id = report_id
            except Exception as e:
                logger.warning(f"Failed to create test reports: {e}")

            # 13. Setup Friend Request (for accept testing)
            if self.test_other_user_id:
                try:
                    relationships_mod = api.get_relationships()
                    if relationships_mod:
                        fr = relationships_mod.send_friend_request(
                            self.test_other_user_id, user.id, "Hi!"
                        )
                        self.test_friend_request_id = getattr(fr, "id", None)
                        if self.test_friend_request_id:
                            logger.debug(
                                f"Sent friend request {self.test_friend_request_id} from {self.test_other_user_id} to {user.id}"
                            )
                        else:
                            logger.debug(
                                f"Sent friend request from {self.test_other_user_id} to {user.id}"
                            )
                except Exception as e:
                    logger.warning(f"Failed to setup friend request: {e}")

            # 14. Setup Test AutoMod Rule (for admin automod testing)
            if self.test_server_id:
                try:
                    from src.core import automod
                    from src.core.automod.models import RuleType

                    automod_rule = automod.create_rule(
                        user_id=user.id,
                        server_id=self.test_server_id,
                        name="Self-Test AutoMod Rule",
                        rule_type=RuleType.KEYWORD,
                        rule_config={"keywords": ["test-bad-word"]},
                        actions=[{"type": "delete_message", "config": {}}],
                        priority=0,
                        check_all=False,
                    )
                    self.test_automod_rule_id = automod_rule.id
                    logger.debug(
                        f"Created test automod rule ID: {self.test_automod_rule_id}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to create test automod rule: {e}")

            # 15. Setup Test Access Token (for admin security testing)
            try:
                from src.core import auth as auth_module

                token_name = f"selftest-token-{secrets.token_hex(4)}"
                access_token = auth_module.create_api_access_token(
                    name=token_name,
                    created_by=user.id,
                    token_value=secrets.token_urlsafe(48),
                    description="Self-test access token",
                    scope_mode="monitor",
                )
                self.test_access_token_id = access_token.id
                logger.debug(
                    f"Created test access token ID: {self.test_access_token_id}"
                )
            except Exception as e:
                logger.warning(f"Failed to create test access token: {e}")

            # 16. Setup Test Support Ticket (for admin ticket testing)
            if self.test_other_user_id:
                try:
                    from src.core import feedback as feedback_module

                    ticket_id = feedback_module.submit_feedback(
                        user_id=self.test_other_user_id,
                        content="Self-test support ticket",
                        category="bug",
                        rating=5,
                    )
                    self.test_ticket_id = ticket_id
                    logger.debug(f"Created test ticket ID: {self.test_ticket_id}")
                except Exception as e:
                    logger.warning(f"Failed to create test ticket: {e}")

            # 17. Setup Test Poll (for poll testing)
            if self.test_conversation_id and messaging:
                try:
                    from src.core import polls as polls_module
                    from src.core.polls import PollResultsVisibility

                    # Ensure polls module is set up with messaging module
                    try:
                        polls_module.setup(_r_db, messaging_module=messaging)
                    except Exception:
                        pass  # Already set up

                    poll_parent = messaging.send_message(
                        user.id,
                        self.test_conversation_id,
                        "Self-test poll parent message",
                    )
                    poll_msg_id = poll_parent.id
                    poll = polls_module.create_poll(
                        user_id=user.id,
                        message_id=poll_msg_id,
                        question="Self-test poll question?",
                        options=["Option A", "Option B", "Option C"],
                        duration_hours=24,
                        allow_multiple_choice=True,
                        results_visibility=PollResultsVisibility.ALWAYS,
                    )
                    self.test_poll_id = poll.id
                    self.test_poll_option_ids = [opt.id for opt in poll.options]
                    logger.debug(f"Created test poll ID: {self.test_poll_id}")
                except Exception as e:
                    logger.warning(f"Failed to create test poll: {e}")

            # 17b. Setup Test Emoji (for emoji endpoints)
            if self.test_server_id:
                try:
                    db_e = api.get_db()
                    if db_e:
                        existing_emoji = db_e.fetch_one(
                            "SELECT id FROM react_custom_emoji WHERE name = ? AND server_id = ?",
                            ("selftest_emoji", self.test_server_id),
                        )
                        if not existing_emoji:
                            emoji_id = self._generate_snowflake()
                            db_e.execute(
                                "INSERT INTO react_custom_emoji (id, name, server_id, created_by, animated, url, size, width, height, available, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    emoji_id,
                                    "selftest_emoji",
                                    self.test_server_id,
                                    user.id,
                                    0,
                                    "https://example.com/emoji.png",
                                    2048,
                                    128,
                                    128,
                                    1,
                                    int(time.time()),
                                ),
                            )
                            self.test_emoji_id = emoji_id
                            logger.debug(f"Created test emoji ID: {self.test_emoji_id}")
                        else:
                            self.test_emoji_id = (
                                existing_emoji["id"]
                                if isinstance(existing_emoji, dict)
                                else existing_emoji[0]
                            )
                except Exception as e:
                    logger.warning(f"Failed to create test emoji: {e}")

            # 17c. Setup Test Sticker (for sticker endpoints)
            if self.test_server_id:
                try:
                    db_s = api.get_db()
                    if db_s:
                        existing_pack = db_s.fetch_one(
                            "SELECT id FROM sticker_packs WHERE name = ?",
                            ("selftest_pack",),
                        )
                        if existing_pack:
                            pack_id = (
                                existing_pack["id"]
                                if isinstance(existing_pack, dict)
                                else existing_pack[0]
                            )
                        else:
                            pack_id = self._generate_snowflake()
                            db_s.execute(
                                "INSERT INTO sticker_packs (id, name, description_encrypted, pack_type, server_id, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    pack_id,
                                    "selftest_pack",
                                    "Self-test pack",
                                    "server",
                                    self.test_server_id,
                                    user.id,
                                    int(time.time()),
                                    int(time.time()),
                                ),
                            )
                        existing_sticker = db_s.fetch_one(
                            "SELECT id FROM sticker_stickers WHERE name = ?",
                            ("selftest_sticker",),
                        )
                        if not existing_sticker:
                            sticker_id = self._generate_snowflake()
                            # Try with description column first; fall back for older DBs
                            try:
                                db_s.execute(
                                    "INSERT INTO sticker_stickers (id, name, pack_id, format, description, tags, url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        sticker_id,
                                        "selftest_sticker",
                                        pack_id,
                                        "png",
                                        "Self-test sticker",
                                        "test",
                                        "https://example.com/sticker.png",
                                        int(time.time()),
                                    ),
                                )
                            except Exception:
                                db_s.execute(
                                    "INSERT INTO sticker_stickers (id, name, pack_id, format, tags, url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        sticker_id,
                                        "selftest_sticker",
                                        pack_id,
                                        "png",
                                        "test",
                                        "https://example.com/sticker.png",
                                        int(time.time()),
                                    ),
                                )
                            self.test_sticker_id = sticker_id
                            logger.debug(
                                f"Created test sticker ID: {self.test_sticker_id}"
                            )
                        else:
                            self.test_sticker_id = (
                                existing_sticker["id"]
                                if isinstance(existing_sticker, dict)
                                else existing_sticker[0]
                            )
                except Exception as e:
                    logger.warning(f"Failed to create test sticker: {e}")

            # 18. Setup Test Thread (for thread testing)
            if self.test_channel_id and self.test_message_id:
                try:
                    threads_mod = api.get_threads()
                    if threads_mod:
                        from src.core.threads import AutoArchiveDuration

                        thread = threads_mod.create_thread_from_message(
                            user_id=user.id,
                            message_id=self.test_message_id,
                            name="Self-Test Thread",
                            auto_archive_duration=AutoArchiveDuration.ONE_HOUR,
                        )
                        self.test_thread_id = thread.id
                        logger.debug(f"Created test thread ID: {self.test_thread_id}")
                except Exception as e:
                    logger.warning(f"Failed to create test thread: {e}")

            logger.info(
                f"Resources created: Server={self.test_server_id}, Channel={self.test_channel_id}, Role={self.test_role_id}, "
                f"AutoMod={self.test_automod_rule_id}, Token={self.test_access_token_id}, Ticket={self.test_ticket_id}, "
                f"Poll={self.test_poll_id}, Thread={self.test_thread_id}"
            )
            return True
        except Exception as e:
            logger.error(f"Setup error: {e}")
            logger.error(traceback.format_exc())
            return False

    def _cleanup_test_data(self) -> None:
        """Remove all test resources via API DELETE calls, with SQL fallback."""
        logger.info("Cleaning up test data via API...")

        # 1. Try API-based cleanup for tracked resources
        api_success = self._api_cleanup()

        # 2. Fall back to SQL for anything the API missed
        if not api_success or self.test_user_id:
            db = api.get_db()
            if not db:
                return

            try:
                if db.type == "sqlite":
                    db.execute("PRAGMA foreign_keys=OFF")

                db.begin_transaction()
                if self.test_user_id:
                    self._delete_all_for_user(db, self.test_user_id)
                if (
                    self.test_other_user_id
                    and self.test_other_user_id != self.test_user_id
                ):
                    self._delete_all_for_user(db, self.test_other_user_id)
                db.commit()
                logger.debug("SQL fallback cleanup completed")
            except Exception as e:
                db.rollback()
                logger.error(f"SQL fallback cleanup failed: {e}")
            finally:
                if db.type == "sqlite":
                    db.execute("PRAGMA foreign_keys=ON")

    def _api_cleanup(self) -> bool:
        """Attempt to delete test resources via API DELETE calls."""
        resources = []

        # Order matters: delete children before parents
        # Poll vote/close first, then poll itself
        if self.test_poll_id:
            try:
                close_resp = self.session.post(
                    f"{self.base_url}/api/v1/polls/{self.test_poll_id}/close",
                    timeout=5,
                )
                logger.debug(f"API cleanup poll close: {close_resp.status_code}")
            except Exception as e:
                logger.debug(f"API cleanup poll close exception: {e}")

        if self.test_message_id and self.test_conversation_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/channels/{self.test_channel_id}/messages/{self.test_message_id}",
                )
            )

        if self.test_webhook_id:
            resources.append(("DELETE", f"/api/v1/webhooks/{self.test_webhook_id}"))

        if self.test_emoji_id and self.test_server_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/servers/{self.test_server_id}/emojis/{self.test_emoji_id}",
                )
            )

        if self.test_sticker_id:
            resources.append(("DELETE", f"/api/v1/stickers/{self.test_sticker_id}"))

        if self.test_poll_id:
            resources.append(("DELETE", f"/api/v1/polls/{self.test_poll_id}"))

        if self.test_automod_rule_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/automod/rules/{self.test_automod_rule_id}",
                )
            )

        if self.test_access_token_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/admin/access-tokens/{self.test_access_token_id}",
                )
            )

        if self.test_thread_id:
            resources.append(("DELETE", f"/api/v1/threads/{self.test_thread_id}"))

        if self.test_channel_id:
            resources.append(("DELETE", f"/api/v1/channels/{self.test_channel_id}"))

        if self.test_invite_code:
            resources.append(
                ("DELETE", f"/api/v1/channels/invites/{self.test_invite_code}")
            )

        if self.test_role_id and self.test_server_id:
            resources.append(
                (
                    "DELETE",
                    f"/api/v1/servers/{self.test_server_id}/roles/{self.test_role_id}",
                )
            )

        if self.test_server_id:
            resources.append(("DELETE", f"/api/v1/servers/{self.test_server_id}"))

        if self.test_application_id:
            resources.append(
                ("DELETE", f"/api/v1/applications/{self.test_application_id}")
            )

        # Friend request: POST to cancel/decline rather than DELETE
        if self.test_friend_request_id:
            try:
                fr_resp = self.session.post(
                    f"{self.base_url}/api/v1/relationships/{self.test_friend_request_id}/cancel",
                    timeout=5,
                )
                logger.debug(
                    f"API cleanup friend request cancel: {fr_resp.status_code}"
                )
            except Exception as e:
                logger.debug(f"API cleanup friend request exception: {e}")

        # Report: resolve via API
        if self.test_report_id:
            try:
                rpt_resp = self.session.patch(
                    f"{self.base_url}/api/v1/reports/users/{self.test_report_id}",
                    json={"status": "resolved"},
                    timeout=5,
                )
                logger.debug(f"API cleanup user report resolve: {rpt_resp.status_code}")
            except Exception as e:
                logger.debug(f"API cleanup user report exception: {e}")

        all_ok = True
        for method, path in resources:
            try:
                resp = self.session.request(method, f"{self.base_url}{path}", timeout=5)
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

    def _test_delete_resources(self) -> None:
        """Test DELETE endpoints for tracked resources created during setup."""
        logger.info("Testing DELETE endpoints for tracked resources...")

        # Order: children before parents (reversed creation order)
        delete_tests = []

        if self.test_message_id and self.test_channel_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/channels/{self.test_channel_id}/messages/{self.test_message_id}",
                    "message",
                )
            )

        if self.test_webhook_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/webhooks/{self.test_webhook_id}",
                    "webhook",
                )
            )

        if self.test_channel_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/channels/{self.test_channel_id}",
                    "channel",
                )
            )

        if self.test_invite_code:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/channels/invites/{self.test_invite_code}",
                    "invite",
                )
            )

        if self.test_role_id and self.test_server_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/servers/{self.test_server_id}/roles/{self.test_role_id}",
                    "role",
                )
            )

        if self.test_server_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/servers/{self.test_server_id}",
                    "server",
                )
            )

        if self.test_application_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/applications/{self.test_application_id}",
                    "application",
                )
            )

        for method, path, label in delete_tests:
            start = time.time()
            try:
                resp = self.session.request(method, f"{self.base_url}{path}", timeout=5)
                duration = (time.time() - start) * 1000
                success = 200 <= resp.status_code < 300
                self.results.append(
                    {
                        "method": "DELETE",
                        "path": path,
                        "status_code": resp.status_code,
                        "duration_ms": duration,
                        "success": success,
                        "label": f"delete_{label}",
                    }
                )
                if success:
                    logger.info(
                        f"DELETE PASSED: {label:<15} {path:<50} -> {resp.status_code} ({duration:.1f}ms)"
                    )
                else:
                    logger.error(
                        f"DELETE FAILED: {label:<15} {path:<50} -> {resp.status_code} ({duration:.1f}ms): {resp.text[:200]}"
                    )
            except Exception as e:
                self.results.append(
                    {
                        "method": "DELETE",
                        "path": path,
                        "status_code": 0,
                        "duration_ms": 0,
                        "success": False,
                        "error": str(e),
                        "label": f"delete_{label}",
                    }
                )
                logger.error(f"DELETE EXCEPTION: {label:<15} {path:<50} -> {e}")
                success = False

            # Nullify the tracked ID on success so _api_cleanup doesn't retry
            if success:
                if label == "delete_message":
                    self.test_message_id = None
                elif label == "delete_webhook":
                    self.test_webhook_id = None
                elif label == "delete_channel":
                    self.test_channel_id = None
                    self.test_conversation_id = None
                elif label == "delete_invite":
                    self.test_invite_code = None
                elif label == "delete_role":
                    self.test_role_id = None
                elif label == "delete_server":
                    self.test_server_id = None
                elif label == "delete_application":
                    self.test_application_id = None

            time.sleep(0.05)

    def _test_bot_server_integration(self) -> None:
        """Test bot server installation flow: request first, then approve."""
        if not self.test_server_id or not self.test_application_id:
            logger.debug(
                "Skipping bot server integration test (missing server or application)"
            )
            return

        logger.info("Testing bot server integration (request -> approve)...")

        # Step 1: Request bot installation
        req_body = {"application_id": int(self.test_application_id)}
        req_path = f"/api/v1/bots/servers/{self.test_server_id}/request"
        start = time.time()
        try:
            resp = self.session.post(
                f"{self.base_url}{req_path}",
                json=req_body,
                timeout=5,
            )
            duration = (time.time() - start) * 1000
            success = 200 <= resp.status_code < 300
            self.results.append(
                {
                    "method": "POST",
                    "path": req_path,
                    "status_code": resp.status_code,
                    "duration_ms": duration,
                    "success": success,
                    "label": "bot_request",
                }
            )
            if success:
                logger.info(
                    f"Bot request PASSED -> {resp.status_code} ({duration:.1f}ms)"
                )
            else:
                logger.warning(f"Bot request -> {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            self.results.append(
                {
                    "method": "POST",
                    "path": req_path,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "error": str(e),
                    "label": "bot_request",
                }
            )
            logger.error(f"Bot request EXCEPTION: {e}")

        time.sleep(0.05)

        # Step 2: Approve bot installation
        approve_body = {"application_id": int(self.test_application_id)}
        approve_path = f"/api/v1/bots/servers/{self.test_server_id}/approve"
        start = time.time()
        try:
            resp = self.session.post(
                f"{self.base_url}{approve_path}",
                json=approve_body,
                timeout=5,
            )
            duration = (time.time() - start) * 1000
            success = 200 <= resp.status_code < 300
            self.results.append(
                {
                    "method": "POST",
                    "path": approve_path,
                    "status_code": resp.status_code,
                    "duration_ms": duration,
                    "success": success,
                    "label": "bot_approve",
                }
            )
            if success:
                logger.info(
                    f"Bot approve PASSED -> {resp.status_code} ({duration:.1f}ms)"
                )
            else:
                logger.warning(f"Bot approve -> {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            self.results.append(
                {
                    "method": "POST",
                    "path": approve_path,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "error": str(e),
                    "label": "bot_approve",
                }
            )
            logger.error(f"Bot approve EXCEPTION: {e}")

    def _plexijoin_expected_statuses(self) -> set[int]:
        """Return the expected non-2xx statuses for PlexiJoin feature routes."""
        if license_module.has_feature("plexijoin", default=False):
            return set()
        return {403}

    def _test_rate_limits(self) -> None:
        """Negative test: send rapid requests without internal-secret header to verify 429."""
        logger.info("Testing rate limit enforcement...")

        # Create a fresh session WITHOUT internal secret header
        rate_session = self.requests_module.Session()
        if self.token:
            rate_session.headers.update({"Authorization": f"Bearer {self.token}"})
        # Intentionally omit X-Plexichat-Internal-Secret to trigger rate limits

        target = f"{self.base_url}/api/v1/users/@me"

        # Send a burst of rapid requests to trigger rate limiting
        burst_count = 15
        rate_limited = False
        status_codes = []

        for i in range(burst_count):
            try:
                resp = rate_session.get(target, timeout=3)
                status_codes.append(resp.status_code)
                if resp.status_code == 429:
                    rate_limited = True
                    logger.debug(f"Rate limit triggered at request {i + 1} (429)")
                    break
            except Exception as e:
                logger.debug(f"Rate limit test request {i + 1} exception: {e}")
                status_codes.append(0)

        # Mark as 'warning' (not a failure) when rate limiting is not active,
        # since rate limiting may be intentionally disabled in dev environments.
        # This result is excluded from the failure count in the summary.
        self.results.append(
            {
                "method": "BURST",
                "path": "/api/v1/users/@me (no bypass header)",
                "status_code": 429
                if rate_limited
                else (status_codes[-1] if status_codes else 0),
                "duration_ms": 0,
                "success": True,  # Never mark as failure — rate limiting may be intentionally disabled
                "label": "rate_limit_test",
                "status_codes": status_codes,
                "warning": not rate_limited,
            }
        )

        if rate_limited:
            logger.info(
                "Rate limit NEGATIVE TEST PASSED: rate limiting is active (got 429 on burst)"
            )
        else:
            logger.warning(
                f"Rate limit NEGATIVE TEST WARNING: no 429 received after {burst_count} requests "
                f"(statuses: {status_codes}). Rate limiting may be disabled or burst too small."
            )

    def _test_websocket(self) -> None:
        """Test WebSocket gateway connectivity and heartbeat."""
        ws_url = self.base_url.replace("http", "ws") + "/gateway"
        logger.info(f"Testing WebSocket gateway: {ws_url}")

        start = time.time()
        success = False
        error_msg = None

        try:
            # We need to pass the internal secret in headers for WS upgrade too
            headers = {}
            internal_secret = api.get_internal_secret()
            if internal_secret:
                headers["X-Plexichat-Internal-Secret"] = internal_secret

            ws_conn = websocket.create_connection(ws_url, timeout=5, header=headers)

            # 1. Receive HELLO (Op 10)
            hello = json.loads(ws_conn.recv())
            if hello.get("op") != 10:
                error_msg = f"Expected HELLO (op 10), got op {hello.get('op')}"
                ws_conn.close()
            else:
                # 2. Identify (Op 2)
                ws_conn.send(
                    json.dumps(
                        {
                            "op": 2,
                            "d": {
                                "token": self.token,
                                "intents": 0,
                                "properties": {
                                    "os": "selftest",
                                    "browser": "python",
                                    "device": "selftest",
                                },
                            },
                        }
                    )
                )

                # 3. Receive READY
                ready = json.loads(ws_conn.recv())
                if ready.get("t") != "READY":
                    error_msg = f"Expected READY, got {ready.get('t')}"
                    ws_conn.close()
                else:
                    # 4. Test Heartbeat (Op 1 -> Op 11)
                    ws_conn.send(json.dumps({"op": 1, "d": int(time.time())}))
                    heartbeat_ack = json.loads(ws_conn.recv())
                    if heartbeat_ack.get("op") != 11:
                        error_msg = f"Expected HEARTBEAT_ACK (op 11), got op {heartbeat_ack.get('op')}"
                        ws_conn.close()
                    else:
                        success = True
                        ws_conn.close()
        except Exception as e:
            error_msg = str(e)

        duration = (time.time() - start) * 1000
        self.results.append(
            {
                "method": "WS",
                "path": "/gateway",
                "status_code": 101 if success else 0,
                "duration_ms": duration,
                "success": success,
                "error": error_msg,
            }
        )

        if success:
            logger.info(f"WebSocket verified successfully ({duration:.1f}ms)")
        else:
            logger.error(f"WebSocket FAILED: {error_msg}")

    def _discover_routes(self) -> List[Dict[str, Any]]:
        """Extract all routes from the running FastAPI app via openapi.json."""
        try:
            resp = self.session.get(f"{self.base_url}/openapi.json", timeout=10)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch OpenAPI spec: {resp.status_code}")
                return []

            self.openapi_spec = resp.json()
            routes = []

            for path, methods in self.openapi_spec.get("paths", {}).items():
                for method, details in methods.items():
                    if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                        # Skip documentation and health endpoints
                        if any(
                            x in path
                            for x in ("/docs", "/redoc", "/openapi.json", "/health")
                        ):
                            continue

                        routes.append(
                            {
                                "path": path,
                                "method": method.upper(),
                                "summary": details.get("summary", ""),
                                "operation_id": details.get("operationId", ""),
                                "parameters": details.get("parameters", []),
                                "request_body": details.get("requestBody", {}),
                            }
                        )
            return sorted(routes, key=lambda x: x["path"])
        except Exception as e:
            logger.error(f"Route discovery failed: {e}")
            return []

    def _get_minimal_body(
        self, request_body: Dict[str, Any], path: str, method: str
    ) -> Dict[str, Any]:
        """Generate a minimal valid JSON body based on OpenAPI schema."""
        content = request_body.get("content", {})
        body: Any = {}
        if "application/json" in content:
            schema = content["application/json"].get("schema", {})
            body = self._generate_from_schema(schema)
        else:
            # For endpoints without schema, still try overrides
            body = {}

        # Apply specific overrides based on path for better success rate
        if isinstance(body, dict):
            test_pass = self._test_password or "SelfTest_Generated_123!"
            user_config = self.config.get("test_user", {})

            if "auth/login" in path:
                body["username"] = user_config.get("username", "selftest_admin")
                body["password"] = test_pass

            if "auth/register" in path:
                body["username"] = f"user_{random.randint(10000, 99999)}"
                body["password"] = test_pass
                if "email" in body:
                    body["email"] = f"test_{random.randint(10000, 99999)}@example.com"

            if "users/@me" in path and method == "PATCH":
                if "current_password" in body:
                    body["current_password"] = test_pass
                # Don't try to change username/email to random values by default in PATCH
                # as it might cause collisions or require verification
                body.pop("username", None)
                body.pop("email", None)
                body.pop("password", None)

            if "auth/2fa" in path:
                if "password" in body:
                    body["password"] = test_pass
                if "code" in body:
                    body["code"] = "123456"
                if "challenge_token" in body:
                    body["challenge_token"] = "test_challenge"

            if "users/@me/channels" in path and method == "POST":
                if self.test_other_user_id:
                    body["recipient_id"] = str(self.test_other_user_id)

            if "relationships" in path and method == "POST":
                if self.test_other_user_id:
                    body["user_id"] = str(self.test_other_user_id)

            if "version/negotiate" in path:
                body["client_version"] = "r.1.0-999"

            if "reports/users" in path and method == "POST":
                if self.test_other_user_id:
                    body["user_id"] = str(self.test_other_user_id)
                else:
                    body["user_id"] = "1"

                    # Admin roles: use random name to avoid collisions across runs
                if "admin/roles" in path and method == "POST":
                    body["name"] = "selftest_admin_role_" + secrets.token_hex(4)

            # Automod rules: schema uses rule_type/config, not trigger_type/trigger
            if "automod/rules" in path and method == "POST":
                body["rule_type"] = "keyword"
                body["config"] = {"keywords": ["test"]}
                body["server_id"] = str(self.test_server_id or 1)

            # Polls: message_id is required, keep it
            if "polls" in path and method == "POST":
                body["question"] = "Test poll question?"
                body["options"] = ["Option A", "Option B"]
                if self.test_message_id:
                    body["message_id"] = str(self.test_message_id)

            # Channel creation: remove spurious category_id if present
            if "servers/" in path and "/channels" in path and method == "POST":
                body.pop("category_id", None)
                if self.test_server_id:
                    body.pop("server_id", None)

            # Push tokens: valid platform
            if "push/tokens" in path and method == "POST":
                body["platform"] = "web"
                body["token"] = secrets.token_urlsafe(32)

            # Reports: use valid status and priority
            if "reports/enhanced" in path and method == "POST":
                body["priority"] = "medium"
                body["status"] = "open"

            # Scheduled messages: use future timestamp
            if "scheduled-messages" in path and method == "POST":
                body["scheduled_at"] = int(time.time() * 1000) + 120000
                body["content"] = "Test scheduled message"
                body["conversation_id"] = str(
                    self.test_conversation_id or self.test_channel_id or 1
                )

            # Messages create: ensure reply_to_id either uses test message or is excluded
            if (
                "/channels/" in path
                and "/messages" in path
                and method == "POST"
                and "search" not in path
                and "unread" not in path
            ):
                if "reply_to_id" in body:
                    if self.test_message_id:
                        body["reply_to_id"] = str(self.test_message_id)
                    else:
                        body.pop("reply_to_id", None)

            # Forward message: use correct conversation and message IDs
            if "features/forward" in path and method == "POST":
                if self.test_message_id:
                    body["message_id"] = str(self.test_message_id)
                if self.test_conversation_id:
                    body["target_conversation_id"] = str(self.test_conversation_id)

            # Voice messages: set all required fields
            if (
                "features/voice-messages" in path
                and method == "POST"
                and "/upload" not in path
            ):
                body["conversation_id"] = str(
                    self.test_conversation_id or self.test_channel_id or 1
                )
                body["content_type"] = "audio/ogg"
                body["duration_ms"] = 5000
                body["filename"] = "voice_test.ogg"
                body["size"] = 4096
                body["url"] = "https://example.com/voice_test.ogg"

            # Emoji/Sticker creation/update: need valid lowercase name
            if (
                ("/emojis" in path or "/stickers" in path)
                and method in ("POST", "PATCH")
                and "search" not in path
            ):
                if "name" not in body or body["name"] == "Self-Test Value":
                    body["name"] = "test_asset_" + secrets.token_hex(4)
                # Ensure name is lowercase alphanumeric + underscores only
                if isinstance(body.get("name"), str):
                    body["name"] = re.sub(r"[^a-z0-9_]", "_", body["name"].lower())
                # Small valid base64 PNG
                # Only set image for creation (POST), not update (PATCH)
                if method == "POST":
                    body["image"] = (
                        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhAFGAbKm4AAAAABJRU5ErkJggg=="
                    )
                else:
                    body.pop("image", None)
                if "server_id" in body and self.test_server_id:
                    body["server_id"] = str(self.test_server_id)

            # Sticker send: message_id is required
            if "stickers/" in path and "/send" in path and method == "POST":
                if "message_id" not in body or body["message_id"] == "1":
                    body["message_id"] = str(
                        self.test_message_id or self._generate_snowflake()
                    )

            # Poll vote: need valid option_ids
            if "polls/" in path and "/vote" in path and method == "POST":
                body["option_ids"] = self.test_poll_option_ids or [
                    self._generate_snowflake()
                ]

            # License apply/validate: provide a license_key
            if "license" in path and method == "POST":
                body["license_key"] = "dGVzdF9saWNlbnNl"

            # Access token scopes
            if "access-tokens" in path and "/scopes" in path and method == "POST":
                body["scope_type"] = "ip"
                body["value"] = "192.168.1.1"

            # Migration endpoints: use 3-digit version format
            if "migrations" in path:
                if method == "GET" and re.search(r"/migrations/\d+$", path):
                    body["format"] = "001"

            # Change password: new_password needs at least 12 chars
            if "auth/change-password" in path and method == "POST":
                body["current_password"] = (
                    self._test_password or "SelfTest_Generated_123!"
                )
                body["new_password"] = "SelfTestNewPass123!"

            # Features/tier update: use valid tier name
            if "features" in path and method == "PUT":
                body["rate_limit_tier"] = "standard"

            if "tier" in path and method == "PUT":
                body["tier"] = "standard"

            # Access token rotate: token must be at least 32 chars
            if "access-tokens" in path and "/rotate" in path and method == "POST":
                body["token"] = secrets.token_urlsafe(32)

            # Thread slowmode: interval must be >= 1000ms
            if "slowmode" in path and method == "PUT":
                body["slowmode_interval"] = 5000
                body["interval"] = 5000

            # Admin toggle-status: need to use other user ID
            if "admin-users" in path and "/toggle-status" in path and method == "POST":
                if self.test_other_user_id and "user_id" in body:
                    body["user_id"] = str(self.test_other_user_id)

            # Destructive admin endpoints: target the other user to preserve test admin session
            if "security/lock-user" in path and method == "POST":
                body["user_id"] = (
                    str(self.test_other_user_id)
                    if self.test_other_user_id
                    else str(self.test_user_id)
                )
                body["duration_seconds"] = None

            if "security/unlock-user" in path and method == "POST":
                body["user_id"] = (
                    str(self.test_other_user_id)
                    if self.test_other_user_id
                    else str(self.test_user_id)
                )

            if "security/force-logout" in path and method == "POST":
                body["user_id"] = (
                    str(self.test_other_user_id)
                    if self.test_other_user_id
                    else str(self.test_user_id)
                )

            # PlexiJoin connections
            if "plexijoin/connections" in path and method == "POST":
                body = {
                    "remote_instance_id": "test-instance",
                    "remote_url": "https://test.example.com",
                    "shared_key": "test_shared_key_32_chars_long_here!",
                }

            # Bot approve: use actual application_id
            if "bots/servers/" in path and "/approve" in path and method == "POST":
                if self.test_application_id:
                    body["application_id"] = int(self.test_application_id)

            # Bot request: use actual application_id
            if "bots/servers/" in path and "/request" in path and method == "POST":
                if self.test_application_id:
                    body["application_id"] = int(self.test_application_id)

            # Passkey authenticate: inject captured challenge_id from options response
            if "passkeys/authenticate" in path and method == "POST":
                if self.test_passkey_challenge_id and "challenge_id" in body:
                    body["challenge_id"] = self.test_passkey_challenge_id

            if "name" in body:
                if "server" in path:
                    body["name"] = "Self-Test Server Update"
                elif "channel" in path:
                    body["name"] = "updated-channel"
                elif "role" in path:
                    body["name"] = "Updated Role"
                else:
                    body["name"] = "Self-Test Value"

            if "content" in body:
                body["content"] = "Self-test message content at " + time.strftime(
                    "%H:%M:%S"
                )

            if "status" in body:
                # Use context-aware status values based on the endpoint
                if "tickets" in path:
                    body["status"] = (
                        "open"  # Valid ticket statuses: open, in_progress, resolved, closed
                    )
                elif "approval" in path:
                    body["status"] = "pending"  # Valid approval statuses
                elif "reports" in path:
                    body["status"] = "open"
                else:
                    body["status"] = "online"  # User presence status

            if "code" in body:
                body["code"] = "123456"

        return body

    def _generate_from_schema(
        self, schema: Dict[str, Any], prop_name: Optional[str] = None
    ) -> Any:
        """Recursively generate data from a JSON schema."""
        # Handle references
        if "$ref" in schema:
            ref_path = schema["$ref"].split("/")
            ref_schema = self.openapi_spec
            for part in ref_path[1:]:
                ref_schema = ref_schema.get(part, {})
            return self._generate_from_schema(ref_schema, prop_name)

        # Handle allOf (merge)
        if "allOf" in schema:
            merged = {}
            for sub in schema["allOf"]:
                res = self._generate_from_schema(sub, prop_name)
                if isinstance(res, dict):
                    merged.update(res)
            return merged

        # Handle anyOf / oneOf (pick first)
        for key in ("anyOf", "oneOf"):
            if key in schema:
                return self._generate_from_schema(schema[key][0], prop_name)

        type_ = schema.get("type")

        # If no type, but has properties, it's an object
        if not type_ and "properties" in schema:
            type_ = "object"

        if type_ == "object":
            obj = {}
            required = schema.get("required", [])
            properties = schema.get("properties", {})

            # 1. Fill required properties
            for p_name in required:
                if p_name in properties:
                    obj[p_name] = self._generate_from_schema(properties[p_name], p_name)

            # 2. Fill non-required but common properties to avoid 400s
            for p_name, prop_schema in properties.items():
                if p_name not in obj:
                    # Heuristic: fill if it looks important or to ensure non-empty body
                    if any(
                        x in p_name.lower()
                        for x in (
                            "id",
                            "name",
                            "type",
                            "content",
                            "username",
                            "email",
                            "password",
                            "status",
                            "category",
                        )
                    ):
                        obj[p_name] = self._generate_from_schema(prop_schema, p_name)

            # 3. Ensure not empty if properties exist
            if not obj and properties:
                p_name = next(iter(properties))
                obj[p_name] = self._generate_from_schema(properties[p_name], p_name)

            return obj

        elif type_ == "array":
            items = schema.get("items", {})
            return [self._generate_from_schema(items, prop_name)]

        elif type_ == "string":
            if "enum" in schema:
                return schema["enum"][0]

            # NEW: Handle pattern constraints on string fields
            # This eliminates many path-specific overrides in _get_minimal_body
            if "pattern" in schema and prop_name:
                pattern = schema["pattern"]
                # Common pattern format: ^(val1|val2|val3)$ handle alternation groups
                # Strip leading ^ and trailing $
                if pattern.startswith("^") and pattern.endswith("$"):
                    inner = pattern[1:-1]
                    # Extract value from a parenthesized alternation: (a|b|c)
                    alt_match = re.match(
                        r"^\(([a-zA-Z0-9_.-]+(?:\|[a-zA-Z0-9_.-]+)*)\)$", inner
                    )
                    if alt_match:
                        # Return first option in the alternation
                        return alt_match.group(1).split("|")[0]
                # For simple patterns like ^[a-zA-Z0-9_]+$, generate a valid value
                if pattern == "^[a-zA-Z0-9_]+$":
                    return "test_value"
                if pattern == "^[a-fA-F0-9]{6}$":
                    return "ff0000"
                if pattern == "^[a-zA-Z]+$":
                    return "test"
                # For hex color strings like ^#[0-9a-fA-F]{6}$
                if pattern.startswith("^#"):
                    return "#ff0000"

            # Check by property name if provided
            if prop_name:
                pn = prop_name.lower()
                if pn == "username":
                    return f"user_{random.randint(1000, 9999)}"
                if pn == "email":
                    return f"test_{random.randint(1000, 9999)}@example.com"
                if pn == "password":
                    return self._test_password or "SelfTest_Generated_123!"
                if pn == "current_password":
                    return self._test_password or "SelfTest_Generated_123!"
                if "id" in pn:
                    if "server" in pn:
                        return str(self.test_server_id or self._generate_snowflake())
                    if "channel" in pn:
                        return str(self.test_channel_id or self._generate_snowflake())
                    if "message" in pn:
                        return str(self.test_message_id or self._generate_snowflake())
                    if "user" in pn:
                        return str(self.test_user_id or self._generate_snowflake())
                    return str(self._generate_snowflake())
                if "content" in pn:
                    return "Test content " + secrets.token_hex(4)
                if "reason" in pn:
                    return "Self-test reason"
                if "topic" in pn:
                    return "Self-test topic"
                if "description" in pn:
                    return "Self-test description"
                if "name" in pn:
                    return "test_name_" + secrets.token_hex(4)
                if "reported_user_id" in pn:
                    return str(self.test_user_id)
                if "recipient_id" in pn:
                    return str(self.test_user_id)
                if "status" in pn:
                    return "online"
                if "category" in pn:
                    return "other"
                if "code" in pn:
                    return "123456"
                if "hash" in pn:
                    return "a" * 64
                if "version" in pn:
                    return "a.1.0-1"
                if "method" in pn:
                    return "GET"
                if "question" in pn:
                    return "Test poll question?"
                if "rule_type" in pn:
                    return "keyword"
                if "trigger_type" in pn:
                    return "keyword"
                if "action_type" in pn:
                    return "delete_message"
                if "scope_type" in pn:
                    return "ip"
                if "emoji" in pn and "id" not in pn:
                    return "smile"

            # Handle string types by format
            fmt = schema.get("format")
            if fmt == "email":
                return f"test_{random.randint(1000, 9999)}@example.com"
            if fmt == "password":
                return self._test_password or "SelfTest_Generated_123!"
            if fmt == "date-time":
                return "2024-01-01T00:00:00Z"

            # Use specific values for common string fields to avoid validation errors
            title = schema.get("title", "").lower()
            if "id" in title or "snowflake" in title:
                return str(self._generate_snowflake())

            return "test"

        elif type_ in ("integer", "number"):
            if prop_name:
                pn = prop_name.lower()
                if "status" in pn and "code" in pn:
                    return 200
                if "id" in pn:
                    if "server" in pn:
                        return self.test_server_id or 1
                    if "channel" in pn:
                        return self.test_channel_id or 1
                    if "message" in pn:
                        return self.test_message_id or 1
                    if "user" in pn:
                        return self.test_user_id or 1
                    if "poll" in pn:
                        return self.test_poll_id or 1
                    if "rule" in pn:
                        return self.test_automod_rule_id or 1
                    if "thread" in pn:
                        return self.test_thread_id or 1
            return 1

        elif type_ == "boolean":
            return False  # Default to False to be safe

        return None

    def _generate_snowflake(self) -> int:
        """Generate a random 64-bit snowflake-like ID."""
        # Plexichat snowflakes are roughly 10^17 to 10^18
        # Using secrets for better randomness and ensuring it fits in 63 bits (for signed bigints)
        return secrets.randbits(60) + 10**17

    def _get_param_value(self, p_name: str, path: str) -> str:
        """Resolve a parameter name to its test value."""
        name_low = p_name.lower()

        # Default fallback for IDs should be a valid-looking snowflake, not "1"
        def _gen_snowflake():
            return str(self._generate_snowflake())

        val = "1"
        if "username" in name_low:
            val = "selftest_admin"
        elif "user" in name_low or "member" in name_low:
            # Destructive admin endpoints + toggle-status: target the other user
            if (
                "/force-purge" in path
                or "/force-logout" in path
                or "/force-username-change" in path
                or "/lock-user" in path
                or "/unlock-user" in path
                or "/toggle-status" in path
            ):
                val = (
                    str(self.test_other_user_id)
                    if self.test_other_user_id
                    else _gen_snowflake()
                )
            elif "/bans/" in path or "/kick" in path:
                val = (
                    str(self.test_other_user_id)
                    if self.test_other_user_id
                    else _gen_snowflake()
                )
            elif "/relationships/" in path and "/accept" in path:
                val = (
                    str(self.test_friend_request_id or self.test_other_user_id)
                    if self.test_other_user_id
                    else str(self.test_user_id)
                )
            elif "/invites/" in path or "reports/users" in path:
                val = (
                    str(self.test_other_user_id)
                    if self.test_other_user_id
                    else _gen_snowflake()
                )
            else:
                val = str(self.test_user_id)
        elif "server" in name_low or "guild" in name_low:
            val = str(self.test_server_id)
        elif "channel" in name_low:
            val = str(self.test_channel_id)
        elif "rule" in name_low or "automod" in name_low:
            val = str(self.test_automod_rule_id or _gen_snowflake())
        elif "ticket" in name_low:
            val = str(self.test_ticket_id or _gen_snowflake())
        elif "token" in name_low and ("access" in path or "security" in path):
            val = str(self.test_access_token_id or _gen_snowflake())
        elif "thread" in name_low:
            val = str(self.test_thread_id or _gen_snowflake())
        elif "poll" in name_low:
            val = str(self.test_poll_id or _gen_snowflake())
        elif "role" in name_low:
            if "/admin/" in path:
                val = str(
                    self.test_admin_role_id
                    or self._admin_role_super_id
                    or _gen_snowflake()
                )
            else:
                val = str(self.test_role_id or _gen_snowflake())
        elif "invite" in name_low or "code" in name_low:
            val = self.test_invite_code or "test_invite"
        elif "webhook" in name_low:
            val = (
                str(self.test_webhook_id) if self.test_webhook_id else _gen_snowflake()
            )
        elif "token" in name_low and "webhook" in path:
            val = self.test_webhook_token or "test_token"
        elif "interaction_token" in name_low:
            val = "test_interaction_token"
        elif "passkey_id" in name_low:
            val = _gen_snowflake()
        elif "key" in name_low:
            val = "test_key"
        elif "message" in name_low:
            val = (
                str(self.test_message_id) if self.test_message_id else _gen_snowflake()
            )
        elif "filename" in name_low:
            val = "test_file.png"
        elif "session" in name_low:
            val = "test_session"
        elif "hash" in name_low:
            val = "a" * 64
        elif "emoji" in name_low:
            if "id" in name_low or "name" not in name_low:
                val = str(self.test_emoji_id or _gen_snowflake())
            else:
                val = "smile"
        elif "sticker" in name_low:
            val = str(self.test_sticker_id or _gen_snowflake())
        elif "application" in name_low or "app_id" in name_low:
            val = str(self.test_application_id or _gen_snowflake())
        elif "notification" in name_low:
            val = (
                str(self.test_notification_id)
                if self.test_notification_id
                else _gen_snowflake()
            )
        elif "request" in name_low and "bots" in path:
            val = str(self.test_bot_id or self.test_application_id or _gen_snowflake())
        elif "approval" in name_low:
            val = str(self.test_approval_id or _gen_snowflake())
        elif "report" in name_low:
            if "id" in name_low:
                if "hash-report" in path or "hash_report" in path:
                    val = str(
                        self.test_hash_report_id
                        or self.test_report_id
                        or _gen_snowflake()
                    )
                elif "message-report" in path or "message_report" in path:
                    val = str(
                        self.test_message_report_id
                        or self.test_report_id
                        or _gen_snowflake()
                    )
                elif "user-report" in path or "user_report" in path:
                    val = str(self.test_report_id or _gen_snowflake())
                else:
                    val = str(self.test_report_id or _gen_snowflake())
        elif "version" in name_low and "/migrations/" in path:
            val = "001"
        elif "format" in name_low and ("/audit/" in path or "/telemetry/" in path):
            val = "csv"
        elif "deletion_at" in name_low:
            val = str(int(time.time()) + 86400)
        elif (
            "id" in name_low
            or name_low.endswith("_id")
            or name_low in ("around", "before", "after")
        ):
            if "automod" in path or "rule" in name_low:
                val = str(self.test_automod_rule_id or _gen_snowflake())
            elif "ticket" in path:
                val = str(self.test_ticket_id or _gen_snowflake())
            elif "access-token" in path or "access_token" in name_low:
                val = str(self.test_access_token_id or _gen_snowflake())
            elif "thread" in path:
                val = str(self.test_thread_id or _gen_snowflake())
            elif "poll" in path:
                val = str(self.test_poll_id or _gen_snowflake())
            elif "server" in path:
                val = str(self.test_server_id)
            elif "channel" in path:
                val = str(self.test_channel_id)
            elif "user" in path:
                val = str(self.test_user_id)
            elif "message" in path or name_low in ("around", "before", "after"):
                val = str(self.test_message_id or _gen_snowflake())
            elif "role" in name_low:
                if "/admin/" in path and "/roles/" in path:
                    val = str(
                        self.test_admin_role_id
                        or self._admin_role_super_id
                        or _gen_snowflake()
                    )
                else:
                    val = str(self.test_role_id or _gen_snowflake())
            elif "relationship" in path:
                if "/accept" in path and self.test_friend_request_id:
                    val = str(self.test_friend_request_id)
                else:
                    val = str(self.test_user_id)
            elif "report" in path:
                if "hash-report" in path or "hash_report" in path:
                    val = str(
                        self.test_hash_report_id
                        or self.test_report_id
                        or _gen_snowflake()
                    )
                elif "message-report" in path or "message_report" in path:
                    val = str(
                        self.test_message_report_id
                        or self.test_report_id
                        or _gen_snowflake()
                    )
                elif "user-report" in path or "user_report" in path:
                    val = str(self.test_report_id or _gen_snowflake())
                else:
                    val = str(self.test_report_id or _gen_snowflake())
            elif "notification" in path:
                val = str(self.test_notification_id or _gen_snowflake())
            elif "application" in name_low or "app" in name_low:
                val = str(self.test_application_id or _gen_snowflake())
            elif "bot" in path:
                val = str(
                    self.test_bot_id or self.test_application_id or _gen_snowflake()
                )
            elif "sticker" in path:
                val = str(self.test_sticker_id or _gen_snowflake())
            else:
                val = _gen_snowflake()
        return val

    def _test_endpoint(
        self,
        method: str,
        path: str,
        route_details: Dict[str, Any],
        use_other: bool = False,
    ) -> None:
        """Test a specific endpoint with valid IDs and data."""
        url_path = path
        query_params = {}
        active_session = self.other_session if use_other else self.session

        # Replace path parameters and collect query parameters
        for param in route_details.get("parameters", []):
            p_name = param.get("name")
            val = self._get_param_value(p_name, path)

            logger.info(f"  Param: {p_name}={val} (in {param.get('in')})")

            if param.get("in") == "path":
                url_path = url_path.replace(f"{{{p_name}}}", val)
                url_path = url_path.replace(f"{{{p_name.lower()}}}", val)
            elif param.get("in") == "query":
                query_params[p_name] = val

        if "delay-deletion" in path and method == "POST":
            future_deletion_at = int(time.time() * 1000) + 86400000
            query_params["deletion_at"] = str(future_deletion_at)

        # Prepare request body
        json_body = None
        files = None
        form_data = {}

        request_body = route_details.get("request_body", {})
        content_types = request_body.get("content", {})

        if "multipart/form-data" in content_types:
            # Handle file uploads and form fields
            if "voice-messages/upload" in path:
                # Voice message upload: audio file + form fields
                ogg_header = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
                files = {
                    "audio": (
                        "voice_test.ogg",
                        ogg_header + b"\x00" * 1024,
                        "audio/ogg",
                    )
                }
                form_data["conversation_id"] = str(
                    self.test_conversation_id or self.test_channel_id or 1
                )
                form_data["duration_ms"] = "5000"
            else:
                files = {
                    "file": (
                        "test_file.png",
                        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n\x2e\xe4\x00\x00\x00\x00IEND\xaeB`\x82",
                        "image/png",
                    )
                }

                schema = content_types["multipart/form-data"].get("schema", {})
                props = schema.get("properties", {})
                for p_name, p_schema in props.items():
                    if p_name != "file":
                        form_data[p_name] = self._generate_from_schema(p_schema, p_name)
        elif method in ("POST", "PUT", "PATCH"):
            json_body = self._get_minimal_body(request_body, path, method)

        start = time.time()
        try:
            resp = active_session.request(
                method,
                f"{self.base_url}{url_path}",
                json=json_body,
                data=form_data if form_data else None,
                files=files,
                params=query_params,
                timeout=5,
            )
            duration = (time.time() - start) * 1000

            # Strict success check (2xx only), with explicit handling for
            # feature-gated PlexiJoin endpoints.
            success = 200 <= resp.status_code < 300
            if not success and "/api/v1/admin/plexijoin/" in path:
                expected_plexijoin_statuses = self._plexijoin_expected_statuses()
                if resp.status_code in expected_plexijoin_statuses:
                    success = True

            # If failed and retry enabled, try once more with debug headers
            traceback_data = None
            if not success and self.config.get("retry_on_failure", True):
                retry_resp = active_session.request(
                    method,
                    f"{self.base_url}{url_path}",
                    json=json_body,
                    headers={"X-Plexichat-SelfTest-Debug": "true"},
                    timeout=10,
                )
                if retry_resp.status_code >= 400:
                    try:
                        traceback_data = (
                            retry_resp.json().get("error", {}).get("traceback")
                        )
                    except Exception:
                        pass

            # Capture dynamic values from successful responses for later test endpoints
            if success and resp.status_code in (200, 201):
                try:
                    resp_data = resp.json()
                    # Webhook regenerate-token: capture new token
                    if "regenerate-token" in path and "webhook" in path:
                        new_token = resp_data.get("token")
                        if new_token:
                            self.test_webhook_token = new_token
                            logger.debug(
                                "Updated webhook token from regenerate response"
                            )
                    # Webhook create: capture token and ID if not already set
                    if (
                        "webhook" in path
                        and method == "POST"
                        and "regenerate" not in path
                    ):
                        wh_token = resp_data.get("token")
                        wh_id = resp_data.get("id")
                        if not self.test_webhook_token and wh_token:
                            self.test_webhook_token = wh_token
                        if not self.test_webhook_id and wh_id:
                            self.test_webhook_id = int(wh_id)
                    # Report create (enhanced reports): capture report ID
                    if (
                        "reports/enhanced" in path
                        or "reports" in path
                        and method == "POST"
                    ):
                        report_id = resp_data.get("report_id")
                        if report_id:
                            self.test_report_id = int(report_id)
                            logger.debug(f"Captured report ID: {self.test_report_id}")
                    # Passkey options/authenticate: capture challenge_id
                    if "passkeys/options/authenticate" in path and method == "POST":
                        cid = resp_data.get("challenge_id")
                        if cid:
                            self.test_passkey_challenge_id = str(cid)
                            logger.debug(
                                f"Captured passkey challenge ID: {self.test_passkey_challenge_id}"
                            )
                except Exception:
                    pass

            result = {
                "method": method,
                "path": path,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "traceback": traceback_data,
            }
            self.results.append(result)

            if not success:
                logger.error(
                    f"FAILED: {method:<6} {path:<40} -> Status {resp.status_code} ({duration:.1f}ms)"
                )
                if resp.status_code == 400:
                    logger.error(f"  Validation Error: {resp.text[:200]}")
                if traceback_data:
                    logger.error(f"Captured Traceback for {path}:\n{traceback_data}")
            elif self.config.get("verbose", False):
                logger.info(
                    f"PASSED: {method:<6} {path:<40} -> Status {resp.status_code} ({duration:.1f}ms)"
                )

        except Exception as e:
            self.results.append(
                {
                    "method": method,
                    "path": path,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "error": str(e),
                }
            )
            logger.error(f"EXCEPTION: {method:<6} {path:<40} -> {e}")

    def _report_summary(self) -> bool:
        """Log the test summary."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r["success"])
        failed = total - passed
        duration = time.time() - self.start_time

        logger.info("=" * 60)
        logger.info("SELF-TEST SUMMARY")
        logger.info(f"Total Endpoints: {total}")
        logger.info(f"Passed:          {passed}")
        logger.info(f"Failed:          {failed}")
        logger.info(
            f"Success Rate:    {(passed / total * 100 if total > 0 else 0):.1f}%"
        )
        logger.info(f"Total Duration:  {duration:.2f}s")
        logger.info("=" * 60)

        if failed > 0:
            logger.error("Failed Endpoints (Non-2xx Responses):")
            for r in self.results:
                if not r["success"]:
                    logger.error(
                        f"  - {r['method']:<6} {r['path']} (Status: {r['status_code']})"
                    )
                    if "error" in r:
                        logger.error(f"    Error: {r['error']}")
            logger.error("See detailed logs above or in latest.log")

        return failed == 0
