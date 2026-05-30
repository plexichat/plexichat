"""Auth setup mixin.

Creates the main test user, creates the other test user,
handles login flows, and blocks reserved usernames.
"""

import secrets
import traceback

import src.api as api
import utils.logger as logger

from .base import SetupServiceBase


class AuthSetupMixin(SetupServiceBase):
    """Creates test users and handles authentication setup."""

    def setup_security_headers(self) -> None:
        internal_secret = api.get_internal_secret()
        if internal_secret:
            self.ctx.session.headers.update(
                {"X-Plexichat-Internal-Secret": internal_secret}
            )
            self.ctx.other_session.headers.update(
                {"X-Plexichat-Internal-Secret": internal_secret}
            )
            logger.debug("Internal security secret added to test session")

    def _ensure_password(self) -> str:
        user_config = self.ctx.config.get("test_user", {})
        pwd = user_config.get("password")
        if not pwd:
            pwd = secrets.token_urlsafe(16)
            self.ctx._test_password = pwd
            logger.info("No self-test password configured; generated random password.")
        else:
            self.ctx._test_password = pwd
        return pwd

    def create_main_user(self) -> bool:
        user_config = self.ctx.config.get("test_user", {})
        username = user_config.get("username", "selftest_admin")
        assert self.ctx._test_password is not None
        password: str = self.ctx._test_password
        email = user_config.get("email", "selftest@plexichat.com")
        logger.info(f"Setting up test user and resources: {username}")

        auth_mod = api.get_auth()
        if not auth_mod:
            logger.error("Auth module not available for self-test")
            return False

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

            self.ctx.test_user_id = user.id
            logger.info(f"Test user ID: {self.ctx.test_user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to create main user: {e}")
            logger.error(traceback.format_exc())
            return False

    def create_other_user(self) -> bool:
        password = self.ctx._test_password
        for _attempt in range(2):
            rand_suffix = secrets.token_hex(4)
            other_username = f"selftest_user_{rand_suffix}"
            other_email = f"other_{rand_suffix}@selftest.plexichat.com"
            try:
                register_resp = self.ctx.session.post(
                    f"{self.ctx.base_url}/api/v1/auth/register",
                    json={
                        "username": other_username,
                        "email": other_email,
                        "password": password,
                    },
                    timeout=10,
                )
                if register_resp.status_code in (200, 201):
                    register_data = register_resp.json()
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
                        self.ctx._setup_other_username = other_username
                        self.ctx._setup_other_email = other_email
                        logger.debug(
                            f"Registered other user via API: {other_username} (id={other_user_id})"
                        )
                        return True
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

        logger.warning("Failed to register other user after 2 attempts")
        return False

    def login_main_user(self) -> bool:
        user_config = self.ctx.config.get("test_user", {})
        username = user_config.get("username", "selftest_admin")
        password = self.ctx._test_password

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
        self.ctx.session.headers.update({"Authorization": f"Bearer {self.ctx.token}"})
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
        return True

    def login_other_user(self) -> None:
        if not self.ctx.test_other_user_id:
            return
        try:
            other_resp = self.ctx.other_session.post(
                f"{self.ctx.base_url}/api/v1/auth/login",
                json={
                    "username": self.ctx._setup_other_username,
                    "password": self.ctx._test_password,
                },
                timeout=10,
            )
            if other_resp.status_code == 200:
                other_data = other_resp.json()
                self.ctx.other_token = other_data.get("token") or other_data.get(
                    "access_token"
                )
                if self.ctx.other_token:
                    self.ctx.other_session.headers.update(
                        {"Authorization": f"Bearer {self.ctx.other_token}"}
                    )
                internal_secret = api.get_internal_secret()
                if internal_secret:
                    self.ctx.other_session.headers.update(
                        {"X-Plexichat-Internal-Secret": internal_secret}
                    )
                logger.debug("Other user logged in and token retrieved")
        except Exception as e:
            logger.warning(f"Failed to login other user: {e}")

    def block_selftest_username_prefix(self) -> None:
        try:
            _bl_db = api.get_db()
            if _bl_db:
                _bl_db.execute(
                    "INSERT OR IGNORE INTO username_blacklist (pattern, is_regex, reason, created_by) VALUES (?, ?, ?, ?)",
                    (
                        "^selftest",
                        True,
                        "Reserved for self-test",
                        self.ctx.test_user_id,
                    ),
                )
                logger.debug("Added ^selftest regex to username blacklist")
        except Exception as e:
            logger.warning(f"Failed to add selftest to username blacklist: {e}")

    def setup_other_session_paths(self) -> None:
        self.ctx._other_session_paths = {
            "/api/v1/channels/invites",
        }
