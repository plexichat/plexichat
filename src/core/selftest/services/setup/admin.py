"""Admin setup mixin.

Grants admin permissions, syncs admin_users table, creates admin
roles (super_admin, support_admin, moderation_admin, test role),
assigns roles, and creates admin approval requests.
"""

import time

import src.api as api
import utils.logger as logger

from .base import SetupServiceBase


class AdminSetupMixin(SetupServiceBase):
    """Sets up admin permissions, roles, and approvals."""

    def grant_admin_permissions(self) -> bool:
        enable_admin = self.ctx.config.get("enable_admin_tests", True)
        if not enable_admin:
            self.ctx.test_user_id = None
            return False

        auth_mod = api.get_auth()
        if not auth_mod:
            logger.error("Auth module not available for admin grant")
            return False

        user_id = self.ctx.test_user_id
        auth_mod.grant_permission(user_id, "admin.*")
        auth_mod.grant_permission(user_id, "*")
        logger.debug("Admin permissions granted to test user")
        return True

    def setup_admin_user_and_roles(self) -> None:
        enable_admin = self.ctx.config.get("enable_admin_tests", True)
        if not enable_admin:
            return

        user_config = self.ctx.config.get("test_user", {})
        username = user_config.get("username", "selftest_admin")
        email = user_config.get("email", "selftest@plexichat.com")
        assert self.ctx._test_password is not None
        password: str = self.ctx._test_password
        user_id = self.ctx.test_user_id

        db = api.get_db()
        if not db:
            logger.warning("DB not available for admin setup")
            return

        try:
            import src.utils.encryption as _setup_enc

            _actual_hash = _setup_enc.hash_password(password)
            db.execute(
                "INSERT OR REPLACE INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp) VALUES (?, ?, ?, ?, ?, 0)",
                (
                    user_id,
                    username,
                    _actual_hash,
                    email,
                    int(time.time()),
                ),
            )
            logger.debug(f"Synced user in admin_users table: {user_id}")
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
                            user_id,
                        ),
                    )
                except Exception as e:
                    if "UNIQUE constraint" in str(e):
                        super_admin_row = db.fetch_one(
                            "SELECT id FROM admin_roles WHERE name = ?",
                            ("super_admin",),
                        )
                        super_admin_id = (
                            super_admin_row["id"] if super_admin_row else super_admin_id
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
                            '{"users.read": true, "users.edit": true, "tickets.*": true, "admin.approvals": true}',
                            int(time.time()),
                            user_id,
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
            try:
                db.execute(
                    "UPDATE admin_roles SET permissions = ? WHERE id = ?",
                    (
                        '{"users.read": true, "users.edit": true, "tickets.*": true, "admin.approvals": true}',
                        support_admin_id,
                    ),
                )
            except Exception:
                pass

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
                            user_id,
                        ),
                    )
                except Exception as e:
                    if "UNIQUE constraint" not in str(e):
                        raise

            assign = db.fetch_one(
                "SELECT 1 FROM admin_role_assignments WHERE admin_id = ? AND role_id = ?",
                (user_id, super_admin_id),
            )
            if not assign:
                db.execute(
                    "INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
                    (user_id, super_admin_id, int(time.time()), user_id),
                )
                logger.debug(f"Assigned super_admin role to admin {user_id}")
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
                        user_id,
                    ),
                )
                self.ctx.test_non_system_role_id = test_role_id
                logger.debug(f"Created non-system test admin role ID: {test_role_id}")
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
                (user_id,),
            )
            if not existing_approval:
                approval_id = self.ctx.data.generate_snowflake()
                db.execute(
                    "INSERT INTO admin_approvals (id, requested_by, action_type, target_type, status, required_approvals, current_approvals, expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        approval_id,
                        user_id,
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

    def setup_other_admin_user(self) -> None:
        enable_admin = self.ctx.config.get("enable_admin_tests", True)
        if not self.ctx.test_other_user_id or not enable_admin:
            return

        other_id = self.ctx.test_other_user_id
        db = api.get_db()
        if not db:
            return

        try:
            existing_other = db.fetch_one(
                "SELECT id FROM admin_users WHERE id = ?", (other_id,)
            )
            if not existing_other:
                import src.utils.encryption as _setup_enc2

                assert self.ctx._test_password is not None
                _actual_hash2 = _setup_enc2.hash_password(self.ctx._test_password)
                db.execute(
                    "INSERT OR IGNORE INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp) VALUES (?, ?, ?, ?, ?, 0)",
                    (
                        other_id,
                        self.ctx._setup_other_username,
                        _actual_hash2,
                        self.ctx._setup_other_email,
                        int(time.time()),
                    ),
                )
            db.execute(
                "UPDATE auth_users SET permissions = ? WHERE id = ?",
                ('{"admin.*": true, "*": true}', other_id),
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
                (other_id, support_admin_id),
            )
            if not assign_other:
                db.execute(
                    "INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
                    (
                        other_id,
                        support_admin_id,
                        int(time.time()),
                        self.ctx.test_user_id,
                    ),
                )
        except Exception as e:
            logger.warning(f"Failed to setup other admin user: {e}")

    def ensure_approval_comments_table(self) -> None:
        try:
            db_cmt = api.get_db()
            if db_cmt:
                db_cmt.execute("""
                    CREATE TABLE IF NOT EXISTS admin_approval_comments (
                        id INTEGER PRIMARY KEY,
                        approval_id INTEGER NOT NULL,
                        admin_id INTEGER NOT NULL,
                        comment TEXT NOT NULL,
                        created_at INTEGER NOT NULL
                    )
                """)
        except Exception:
            pass
