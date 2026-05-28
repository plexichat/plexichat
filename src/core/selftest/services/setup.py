"""
Setup service for SelfTestRunner.

Creates and configures test users, servers, channels, messages, roles,
invites, applications, bots, and all other resources for endpoint testing.
"""

import time
import traceback
import secrets

import src.api as api
import utils.config as config_mod
import utils.logger as logger

from ..context import SelfTestContext


class SetupService:
    """Creates all test resources needed for the selftest run."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def run_setup(self) -> bool:
        # Initialize variables for pyright
        super_admin_id = None
        other_username = None
        other_email = None
        other_password = None

        user_config = self.ctx.config.get("test_user", {})
        username = user_config.get("username", "selftest_admin")

        self.ctx._test_password = user_config.get("password")
        if not self.ctx._test_password:
            self.ctx._test_password = secrets.token_urlsafe(16)
            logger.info("No self-test password configured; generated random password.")
        password = self.ctx._test_password

        email = user_config.get("email", "selftest@plexichat.com")

        logger.info(f"Setting up test user and resources: {username}")

        internal_secret = api.get_internal_secret()
        if internal_secret:
            self.ctx.session.headers.update(
                {"X-Plexichat-Internal-Secret": internal_secret}
            )
            self.ctx.other_session.headers.update(
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

        # Note: We intentionally do NOT wrap setup in a single DB transaction.
        # The server runs in a background thread, and SQLite serializes writes
        # across connections. Holding a transaction in the main thread while
        # making HTTP calls to the background thread would deadlock.
        try:
            user = auth_mod.get_user_by_username(username)
            if not user:
                logger.debug(f"Creating new test user: {username}")
                try:
                    user = auth_mod.register_selftest(username, email, password)
                except Exception as e:
                    if "Email already registered" in str(e):
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

            # Ensure the test user's password in the DB always matches
            # _test_password so the standalone login test succeeds.
            _pwd_db = api.get_db()
            if _pwd_db:
                try:
                    import src.utils.encryption as _setup_enc_pwd

                    _actual_hash = _setup_enc_pwd.hash_password(password)
                    _pwd_db.execute(
                        "UPDATE auth_users SET password_hash = ? WHERE id = ?",
                        (_actual_hash, user.id),
                    )
                except Exception as _e:
                    logger.warning(f"Could not update test user password: {_e}")

            _cleanup_db = api.get_db()
            if _cleanup_db:
                try:
                    _cleanup_db.execute(
                        "UPDATE auth_users SET account_locked = 0, locked_until = NULL WHERE id = ?",
                        (user.id,),
                    )
                except Exception:
                    pass

            enable_admin = self.ctx.config.get("enable_admin_tests", True)
            if enable_admin:
                auth_mod.grant_permission(user.id, "admin.*")
                auth_mod.grant_permission(user.id, "*")
                logger.debug("Admin permissions granted to test user")

            self.ctx.test_user_id = user.id
            logger.info(f"Test user ID: {self.ctx.test_user_id}")
            if enable_admin:
                db = api.get_db()
                if db:
                    try:
                        import src.utils.encryption as _setup_enc

                        _actual_hash = _setup_enc.hash_password(password)
                        db.execute(
                            "INSERT OR REPLACE INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp) VALUES (?, ?, ?, ?, ?, 0)",
                            (
                                user.id,
                                username,
                                _actual_hash,
                                email,
                                int(time.time()),
                            ),
                        )
                        logger.debug(f"Synced user in admin_users table: {user.id}")
                    except Exception as e:
                        logger.warning(f"Failed to add user to admin_users: {e}")

                    try:
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
                            super_admin_id = self.ctx.data.generate_snowflake()
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
                            support_admin_id = self.ctx.data.generate_snowflake()
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

                        mod_admin_row = db.fetch_one(
                            "SELECT id FROM admin_roles WHERE name = ?",
                            ("moderation_admin",),
                        )
                        if not mod_admin_row:
                            mod_admin_id = self.ctx.data.generate_snowflake()
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
                            if super_admin_id:
                                self.ctx._admin_role_super_id = super_admin_id
                                if not self.ctx.test_admin_role_id:
                                    self.ctx.test_admin_role_id = super_admin_id

                        test_role_row = db.fetch_one(
                            "SELECT id FROM admin_roles WHERE name = ?",
                            ("selftest_test_role",),
                        )
                        if not test_role_row:
                            test_role_id = self.ctx.data.generate_snowflake()
                            db.execute(
                                "INSERT INTO admin_roles (id, name, description, permissions, created_at, created_by, is_system) VALUES (?, ?, ?, ?, ?, ?, 0)",
                                (
                                    test_role_id,
                                    "selftest_test_role",
                                    "Non-system test role for selftest PUT",
                                    '{"users.read": true}',
                                    int(time.time()),
                                    user.id,
                                ),
                            )
                            self.ctx.test_non_system_role_id = test_role_id
                            logger.debug(
                                f"Created non-system test admin role ID: {test_role_id}"
                            )
                        else:
                            self.ctx.test_non_system_role_id = (
                                test_role_row["id"]
                                if isinstance(test_role_row, dict)
                                else test_role_row[0]
                            )
                    except Exception as e:
                        logger.warning(f"Failed to seed admin roles: {e}")

                    try:
                        existing_approval = db.fetch_one(
                            "SELECT id FROM admin_approvals WHERE requested_by = ?",
                            (user.id,),
                        )
                        if not existing_approval:
                            approval_id = self.ctx.data.generate_snowflake()
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
                            self.ctx.test_approval_id = approval_id
                            logger.debug("Created test admin approval request")
                        else:
                            self.ctx.test_approval_id = (
                                existing_approval["id"]
                                if isinstance(existing_approval, dict)
                                else existing_approval[0]
                            )
                    except Exception as e:
                        logger.warning(f"Failed to seed admin approval: {e}")

            self.ctx.test_other_user_id = None
            other_password = password
            for _attempt in range(2):
                rand_suffix = secrets.token_hex(4)
                other_username = f"selftest_user_{rand_suffix}"
                other_email = f"other_{rand_suffix}@selftest.plexichat.com"
                try:
                    # Use the API so we test the real register endpoint.
                    # X-Plexichat-Internal-Secret sets is_internal=True in request.state,
                    # which bypasses the username blacklist check (^selftest pattern).
                    register_resp = self.ctx.session.post(
                        f"{self.ctx.base_url}/api/v1/auth/register",
                        json={
                            "username": other_username,
                            "email": other_email,
                            "password": other_password,
                        },
                        timeout=10,
                    )
                    if register_resp.status_code in (200, 201):
                        register_data = register_resp.json()
                        # LoginResponse has status, token, user -> UserResponse.id
                        user_obj = register_data.get("user")
                        other_user_id = (user_obj or {}).get("id") if user_obj else None
                        if other_user_id is not None:
                            other_user_id = int(other_user_id)
                            auth_db = api.get_db()
                            if auth_db:
                                try:
                                    auth_db.execute(
                                        "UPDATE auth_users SET account_locked = 0, locked_until = NULL WHERE id = ?",
                                        (other_user_id,),
                                    )
                                except Exception:
                                    pass
                            self.ctx.test_other_user_id = other_user_id
                            logger.debug(
                                f"Registered other user via API: {other_username} (id={other_user_id})"
                            )
                            break
                        else:
                            logger.warning(
                                f"Register other user attempt {_attempt + 1}: response missing user.id (status={register_data.get('status')}, keys={list(register_data.keys())})"
                            )
                    else:
                        logger.warning(
                            f"Register other user attempt {_attempt + 1} failed: {register_resp.status_code}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Register other user attempt {_attempt + 1} exception: {e}"
                    )

            if self.ctx.test_other_user_id:
                db = api.get_db()
                _other_id = self.ctx.test_other_user_id
                if enable_admin and db:
                    try:
                        existing_other = db.fetch_one(
                            "SELECT id FROM admin_users WHERE id = ?", (_other_id,)
                        )
                        if not existing_other:
                            import src.utils.encryption as _setup_enc2

                            _actual_hash2 = _setup_enc2.hash_password(password)
                            db.execute(
                                "INSERT OR IGNORE INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp) VALUES (?, ?, ?, ?, ?, 0)",
                                (
                                    _other_id,
                                    other_username,
                                    _actual_hash2,
                                    other_email,
                                    int(time.time()),
                                ),
                            )
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
                            (_other_id, support_admin_id),
                        )
                        if not assign_other:
                            db.execute(
                                "INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
                                (
                                    _other_id,
                                    support_admin_id,
                                    int(time.time()),
                                    user.id,
                                ),
                            )
                        auth_mod.grant_permission(_other_id, "admin.*")
                        assign_other_super = db.fetch_one(
                            "SELECT 1 FROM admin_role_assignments WHERE admin_id = ? AND role_id = ?",
                            (_other_id, super_admin_id),
                        )
                        if not assign_other_super:
                            db.execute(
                                "INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
                                (_other_id, super_admin_id, int(time.time()), user.id),
                            )
                    except Exception as e:
                        logger.warning(f"Failed to setup other admin user: {e}")
                logger.debug(f"Test other user ID: {self.ctx.test_other_user_id}")

            resp = self.ctx.session.post(
                f"{self.ctx.base_url}/api/v1/auth/login",
                json={"username": username, "password": password},
                timeout=10,
            )

            if resp.status_code != 200:
                logger.error(f"Login failed: {resp.text}")
                return False

            login_data = resp.json()
            self.ctx.token = login_data.get("token") or login_data.get("access_token")
            if not self.ctx.token:
                logger.error(f"No token in login response: {login_data}")
                return False
            self.ctx.session.headers.update(
                {"Authorization": f"Bearer {self.ctx.token}"}
            )
            logger.debug("Logged in and token retrieved")

            verify = self.ctx.session.get(
                f"{self.ctx.base_url}/api/v1/users/@me", timeout=5
            )
            if verify.status_code != 200:
                logger.error(
                    f"Token verification failed: {verify.status_code} {verify.text[:200]}"
                )
                logger.error(f"Login response keys: {list(login_data.keys())}")
                return False
            logger.debug("Token verified: user @me OK")

            if self.ctx.test_other_user_id:
                try:
                    other_resp = self.ctx.other_session.post(
                        f"{self.ctx.base_url}/api/v1/auth/login",
                        json={"username": other_username, "password": other_password},
                        timeout=10,
                    )
                    if other_resp.status_code == 200:
                        other_data = other_resp.json()
                        self.ctx.other_token = other_data.get(
                            "token"
                        ) or other_data.get("access_token")
                        if self.ctx.other_token:
                            self.ctx.other_session.headers.update(
                                {"Authorization": f"Bearer {self.ctx.other_token}"}
                            )
                        if internal_secret:
                            self.ctx.other_session.headers.update(
                                {"X-Plexichat-Internal-Secret": internal_secret}
                            )
                        logger.debug("Other user logged in and token retrieved")
                except Exception as e:
                    logger.warning(f"Failed to login other user: {e}")

            self.ctx._other_session_paths = {
                "/api/v1/channels/invites",
            }

            # Block selftest_ prefix in username blacklist so real users can't take test usernames
            try:
                _bl_db = api.get_db()
                if _bl_db:
                    _bl_db.execute(
                        "INSERT OR IGNORE INTO username_blacklist (pattern, is_regex, reason, created_by) VALUES (?, ?, ?, ?)",
                        ("^selftest", True, "Reserved for self-test", user.id),
                    )
                    logger.debug("Added ^selftest regex to username blacklist")
            except Exception as e:
                logger.warning(f"Failed to add selftest to username blacklist: {e}")

            logger.info("Creating test server...")
            server = servers_mod.create_server(
                user.id, "Self-Test Server", "Temporary server for API testing"
            )
            self.ctx.test_server_id = server.id
            logger.info(f"Test server ID: {self.ctx.test_server_id}")

            logger.info("Creating test channel...")
            channel = servers_mod.create_channel(user.id, server.id, "test-channel")
            self.ctx.test_channel_id = channel.id
            self.ctx.test_conversation_id = getattr(channel, "conversation_id", None)
            logger.info(
                f"Test channel ID: {self.ctx.test_channel_id}, Conv ID: {self.ctx.test_conversation_id}"
            )

            if self.ctx.test_conversation_id and messaging:
                logger.info("Creating test message...")
                try:
                    msg = messaging.send_message(
                        user.id,
                        self.ctx.test_conversation_id,
                        "Self-test reference message",
                    )
                    self.ctx.test_message_id = msg.id
                    logger.info(f"Test message ID: {self.ctx.test_message_id}")
                except Exception as e:
                    logger.warning(f"Failed to create test message: {e}")

            logger.debug("Creating test role...")
            role = servers_mod.create_role(
                user.id, server.id, f"Test Role {secrets.token_hex(4)}", color="#ff0000"
            )
            self.ctx.test_role_id = role.id
            logger.debug(f"Test role ID: {self.ctx.test_role_id}")

            logger.debug("Creating test invite...")
            try:
                invite = servers_mod.create_invite(user.id, self.ctx.test_channel_id)
                self.ctx.test_invite_code = invite.code
            except Exception as e:
                logger.warning(f"Failed to create test invite: {e}")
                self.ctx.test_invite_code = None
            logger.debug(f"Test invite code: {self.ctx.test_invite_code}")

            # Pre-join other_user to the server via the API invite-join endpoint.
            # The auto-loop excludes POST channels/invites/{code} to avoid
            # double-testing, so we exercise it here.
            if (
                self.ctx.test_other_user_id
                and self.ctx.test_server_id
                and self.ctx.test_invite_code
            ):
                join_url = f"{self.ctx.base_url}/api/v1/channels/invites/{self.ctx.test_invite_code}"
                try:
                    join_resp = self.ctx.other_session.post(join_url, timeout=10)
                    logger.info(
                        f"Server join POST {join_url} -> {join_resp.status_code} {join_resp.text[:300]}"
                    )
                    if join_resp.status_code not in (200, 201, 204):
                        logger.warning(
                            f"Server join failed: {join_resp.status_code} {join_resp.text[:300]}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to join other user to server: {e}")

            if webhooks_mod:
                logger.debug("Creating test webhook...")
                try:
                    webhook = webhooks_mod.create_webhook(
                        user.id, self.ctx.test_channel_id, "Self-Test Webhook"
                    )
                    self.ctx.test_webhook_id = webhook.id
                    self.ctx.test_webhook_token = getattr(webhook, "token", None)
                    logger.debug(f"Test webhook ID: {self.ctx.test_webhook_id}")
                except Exception as e:
                    logger.warning(f"Failed to create test webhook: {e}")

            # 16. Setup Test Ticket (for ticket testing)
            try:
                from pathlib import Path

                media_dir = Path.home() / ".plexichat" / "media" / "attachments"
                media_dir.mkdir(parents=True, exist_ok=True)
                test_file = media_dir / "test_file.png"
                png_data = (
                    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
                    b"\x01\x00\x00\x05\x00\x01\r\n\x2e\xe4\x00\x00\x00\x00IEND\xaeB`\x82"
                )
                test_file.write_bytes(png_data)
                logger.debug("Created dummy test file at %s", test_file)

                db_mf = api.get_db()
                if db_mf:
                    existing_mf = db_mf.fetch_one(
                        "SELECT id FROM media_files WHERE filename = ? AND deleted = 0",
                        ("test_file.png",),
                    )
                    if not existing_mf:
                        mf_id = self.ctx.data.generate_snowflake()
                        db_mf.execute(
                            "INSERT INTO media_files (id, filename, original_filename, content_type, size, media_type, storage_backend, storage_path, checksum, uploaded_by, uploaded_at, metadata, scan_status, scan_result, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                            (
                                mf_id,
                                "test_file.png",
                                "test_file.png",
                                "image/png",
                                len(png_data),
                                "image",
                                "local",
                                str(test_file),
                                "test_checksum_for_selftest",
                                user.id,
                                int(time.time()),
                                "{}",
                                "clean",
                                "{}",
                            ),
                        )
                        logger.debug(
                            f"Inserted media_files DB record for test_file.png (id={mf_id})"
                        )
            except Exception as e:
                logger.warning(f"Failed to create dummy test file: {e}")

            try:
                from pathlib import Path as _Path

                log_dir_raw = config_mod.get("media", {}).get("logs_dir")
                if log_dir_raw:
                    log_dir = _Path(log_dir_raw).expanduser()
                else:
                    log_dir = _Path.home() / ".plexichat" / "logs"
                if log_dir.exists():
                    log_files = sorted(
                        [
                            f
                            for f in log_dir.iterdir()
                            if f.is_file()
                            and (f.suffix == ".log" or f.name.endswith(".log.zip"))
                        ],
                        key=lambda f: f.stat().st_mtime,
                        reverse=True,
                    )
                    if log_files:
                        self.ctx.test_log_filename = log_files[0].name
                        logger.debug(
                            f"Resolved latest log filename: {self.ctx.test_log_filename}"
                        )
            except Exception as e:
                logger.warning(f"Failed to resolve log filename: {e}")

            settings_mod = api.get_settings()
            if settings_mod:
                try:
                    settings_mod.set_setting(user.id, "test_key", "test_value")
                    logger.debug("Created test setting 'test_key'")
                except Exception as e:
                    logger.warning(f"Failed to create test setting: {e}")

            applications_mod = api.get_applications()
            if applications_mod:
                try:
                    app_name = f"Self-Test App {secrets.token_hex(4)}"
                    app = applications_mod.create_application(user.id, app_name)
                    self.ctx.test_application_id = app.id
                    logger.debug(
                        f"Created test application ID: {self.ctx.test_application_id}"
                    )
                    try:
                        bot = applications_mod.create_bot_for_application(
                            user.id, self.ctx.test_application_id
                        )
                        self.ctx.test_bot_id = (
                            bot["bot_id"] if isinstance(bot, dict) else bot.id
                        )
                        logger.debug(f"Created test bot ID: {self.ctx.test_bot_id}")
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

                if self.ctx.test_application_id:
                    try:
                        applications_mod.update_bot_profile(
                            application_id=self.ctx.test_application_id,
                            user_id=user.id,
                            description="Self-test bot profile",
                        )
                        logger.debug(
                            f"Created bot profile for app {self.ctx.test_application_id}"
                        )
                    except Exception as e2:
                        logger.warning(f"Failed to create bot profile: {e2}")

            _n_db = api.get_db()
            try:
                if _n_db:
                    existing_notif = _n_db.fetch_one(
                        "SELECT id FROM notif_notifications WHERE user_id = ?",
                        (user.id,),
                    )
                    if not existing_notif:
                        notif_id = self.ctx.data.generate_snowflake()
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
                        self.ctx.test_notification_id = notif_id
                        logger.debug("Created test notification")
                    else:
                        self.ctx.test_notification_id = (
                            existing_notif["id"]
                            if isinstance(existing_notif, dict)
                            else existing_notif[0]
                        )
            except Exception as e:
                logger.warning(f"Failed to create test notification: {e}")

            _r_db = api.get_db()
            try:
                if _r_db:
                    existing_report = _r_db.fetch_one(
                        "SELECT id FROM user_reports WHERE reporter_id = ?", (user.id,)
                    )
                    if not existing_report and self.ctx.test_other_user_id:
                        report_id = self.ctx.data.generate_snowflake()
                        _r_db.execute(
                            "INSERT INTO user_reports (id, reporter_id, reported_user_id, reason, category, status, reported_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                report_id,
                                user.id,
                                self.ctx.test_other_user_id,
                                "Self-test report",
                                "other",
                                "open",
                                int(time.time()),
                            ),
                        )
                        logger.debug("Created test user report")
                        hash_report_id = self.ctx.data.generate_snowflake()
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
                        self.ctx.test_hash_report_id = hash_report_id
                        logger.debug("Created test hash report")
                        if self.ctx.test_message_id and self.ctx.test_other_user_id:
                            msg_report_id = self.ctx.data.generate_snowflake()
                            _r_db.execute(
                                "INSERT OR IGNORE INTO message_reports (id, reporter_id, message_id, channel_id, reported_user_id, reason, category, status, reported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    msg_report_id,
                                    user.id,
                                    self.ctx.test_message_id,
                                    self.ctx.test_channel_id or 0,
                                    self.ctx.test_other_user_id,
                                    "Self-test message report",
                                    "other",
                                    "open",
                                    int(time.time()),
                                ),
                            )
                            self.ctx.test_message_report_id = msg_report_id
                            logger.debug("Created test message report")
                        self.ctx.test_report_id = report_id
            except Exception as e:
                logger.warning(f"Failed to create test reports: {e}")

            if self.ctx.test_server_id:
                try:
                    from src.core import automod
                    from src.core.automod.models import RuleType

                    automod_rule = automod.create_rule(
                        user_id=user.id,
                        server_id=self.ctx.test_server_id,
                        name="Self-Test AutoMod Rule",
                        rule_type=RuleType.KEYWORD,
                        rule_config={"keywords": ["test-bad-word"]},
                        actions=[{"type": "delete_message", "config": {}}],
                        priority=0,
                        check_all=False,
                    )
                    self.ctx.test_automod_rule_id = automod_rule.id
                    logger.debug(
                        f"Created test automod rule ID: {self.ctx.test_automod_rule_id}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to create test automod rule: {e}")

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
                self.ctx.test_access_token_id = access_token.id
                logger.debug(
                    f"Created test access token ID: {self.ctx.test_access_token_id}"
                )
            except Exception as e:
                logger.warning(f"Failed to create test access token: {e}")

            if self.ctx.test_other_user_id:
                try:
                    from src.core import feedback as feedback_module

                    ticket_id = feedback_module.submit_feedback(
                        user_id=self.ctx.test_other_user_id,
                        content="Self-test support ticket",
                        category="bug",
                        rating=5,
                    )
                    self.ctx.test_ticket_id = ticket_id
                    logger.debug(f"Created test ticket ID: {self.ctx.test_ticket_id}")
                except Exception as e:
                    logger.warning(f"Failed to create test ticket: {e}")

            if self.ctx.test_conversation_id and messaging:
                try:
                    from src.core import polls as polls_module
                    from src.core.polls import PollResultsVisibility

                    # polls_module.setup() is intentionally NOT called here —
                    # the module is already initialized by the server startup sequence.
                    # Calling .setup() again would re-register handlers and could corrupt state.

                    poll_parent = messaging.send_message(
                        user.id,
                        self.ctx.test_conversation_id,
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
                    self.ctx.test_poll_id = poll.id
                    self.ctx.test_poll_option_ids = [opt.id for opt in poll.options]
                    logger.debug(f"Created test poll ID: {self.ctx.test_poll_id}")
                except Exception as e:
                    logger.warning(f"Failed to create test poll: {e}")

            # Send a friend request from other_user -> admin via the API.
            if self.ctx.test_other_user_id and self.ctx.test_user_id:
                fr_url = f"{self.ctx.base_url}/api/v1/relationships"
                fr_payload = {
                    "user_id": self.ctx.test_user_id,
                    "message": "Self-test friend request",
                }
                try:
                    fr_resp = self.ctx.other_session.post(
                        fr_url, json=fr_payload, timeout=10
                    )
                    logger.info(
                        f"Friend request POST {fr_url} -> {fr_resp.status_code} {fr_resp.text[:300]}"
                    )
                    if fr_resp.status_code in (200, 201):
                        # POST /relationships returns RelationshipResponse (no id field),
                        # so we must look up the request ID from the DB.
                        _fr_lookup = api.get_db()
                        if _fr_lookup:
                            _fr_row = _fr_lookup.fetch_one(
                                "SELECT id FROM rel_friend_requests WHERE sender_id = ? AND recipient_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                                (self.ctx.test_other_user_id, self.ctx.test_user_id),
                            )
                            if _fr_row:
                                self.ctx.test_friend_request_id = (
                                    int(_fr_row["id"])
                                    if isinstance(_fr_row, dict)
                                    else int(_fr_row[0])
                                )
                                logger.info(
                                    f"Friend request created (id={self.ctx.test_friend_request_id})"
                                )
                            else:
                                logger.warning(
                                    "POST /relationships succeeded (200) but no pending request found in DB"
                                )
                        else:
                            logger.warning("DB not available for friend request lookup")
                    elif fr_resp.status_code == 409:
                        logger.warning(
                            "Friend request already exists (409); re-fetching"
                        )
                        _fr_db = api.get_db()
                        if _fr_db:
                            _fr_row = _fr_db.fetch_one(
                                "SELECT id FROM rel_friend_requests WHERE sender_id = ? AND recipient_id = ? AND status = 'pending'",
                                (self.ctx.test_other_user_id, self.ctx.test_user_id),
                            )
                            if _fr_row:
                                self.ctx.test_friend_request_id = (
                                    int(_fr_row["id"])
                                    if isinstance(_fr_row, dict)
                                    else int(_fr_row[0])
                                )
                                logger.info(
                                    f"Found existing friend request (id={self.ctx.test_friend_request_id})"
                                )
                            else:
                                logger.warning(
                                    "409 returned but no pending request found in DB"
                                )
                    else:
                        logger.warning(
                            f"Friend request failed: {fr_resp.status_code} {fr_resp.text[:300]}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to create friend request: {e}")

            if self.ctx.test_server_id:
                try:
                    db_e = api.get_db()
                    if db_e:
                        existing_emoji = db_e.fetch_one(
                            "SELECT id FROM react_custom_emoji WHERE name = ? AND server_id = ?",
                            ("selftest_emoji", self.ctx.test_server_id),
                        )
                        if not existing_emoji:
                            emoji_id = self.ctx.data.generate_snowflake()
                            db_e.execute(
                                "INSERT INTO react_custom_emoji (id, name, server_id, created_by, animated, url, size, width, height, available, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    emoji_id,
                                    "selftest_emoji",
                                    self.ctx.test_server_id,
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
                            self.ctx.test_emoji_id = emoji_id
                            logger.debug(
                                f"Created test emoji ID: {self.ctx.test_emoji_id}"
                            )
                        else:
                            self.ctx.test_emoji_id = (
                                existing_emoji["id"]
                                if isinstance(existing_emoji, dict)
                                else existing_emoji[0]
                            )
                except Exception as e:
                    logger.warning(f"Failed to create test emoji: {e}")

            if self.ctx.test_server_id:
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
                            pack_id = self.ctx.data.generate_snowflake()
                            db_s.execute(
                                "INSERT INTO sticker_packs (id, name, description_encrypted, pack_type, server_id, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    pack_id,
                                    "selftest_pack",
                                    "Self-test pack",
                                    "server",
                                    self.ctx.test_server_id,
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
                            sticker_id = self.ctx.data.generate_snowflake()
                            try:
                                db_s.execute(
                                    "INSERT INTO sticker_stickers (id, name, pack_id, format, description, tags, url, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        sticker_id,
                                        "selftest_sticker",
                                        pack_id,
                                        "png",
                                        "Self-test sticker",
                                        '["test"]',
                                        "https://example.com/sticker.png",
                                        1024,
                                        int(time.time()),
                                    ),
                                )
                            except Exception:
                                db_s.execute(
                                    "INSERT INTO sticker_stickers (id, name, pack_id, format, tags, url, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        sticker_id,
                                        "selftest_sticker",
                                        pack_id,
                                        "png",
                                        '["test"]',
                                        "https://example.com/sticker.png",
                                        1024,
                                        int(time.time()),
                                    ),
                                )
                            self.ctx.test_sticker_id = sticker_id
                            logger.debug(
                                f"Created test sticker ID: {self.ctx.test_sticker_id}"
                            )
                        else:
                            self.ctx.test_sticker_id = (
                                existing_sticker["id"]
                                if isinstance(existing_sticker, dict)
                                else existing_sticker[0]
                            )
                except Exception as e:
                    logger.warning(f"Failed to create test sticker: {e}")

            if self.ctx.test_channel_id and self.ctx.test_message_id:
                try:
                    threads_mod = api.get_threads()
                    if threads_mod:
                        from src.core.threads import AutoArchiveDuration

                        thread = threads_mod.create_thread_from_message(
                            user_id=user.id,
                            message_id=self.ctx.test_message_id,
                            name="Self-Test Thread",
                            auto_archive_duration=AutoArchiveDuration.ONE_HOUR,
                        )
                        self.ctx.test_thread_id = thread.id
                        logger.debug(
                            f"Created test thread ID: {self.ctx.test_thread_id}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to create test thread: {e}")

            logger.info(
                f"Resources created: Server={self.ctx.test_server_id}, Channel={self.ctx.test_channel_id}, Role={self.ctx.test_role_id}, "
                f"AutoMod={self.ctx.test_automod_rule_id}, Token={self.ctx.test_access_token_id}, Ticket={self.ctx.test_ticket_id}, "
                f"Poll={self.ctx.test_poll_id}, Thread={self.ctx.test_thread_id}"
            )

            return True
        except Exception as e:
            logger.error(f"Setup error: {e}")
            logger.error(traceback.format_exc())
            return False
