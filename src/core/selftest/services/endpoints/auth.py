"""Auth endpoint tester mixin.

Tests login, register, sessions, logout, and revoke-all in standalone mode.
"""

import time
import random

import src.api as api
import utils.logger as logger

from .base import EndpointTesterBase


class AuthMixin(EndpointTesterBase):
    """Tests auth-related API endpoints."""

    def test_auth_endpoints(self) -> None:
        """Test auth login, logout, register, and sessions/revoke-all in standalone mode."""
        if not self.ctx.standalone_mode:
            return

        username = self.ctx.config.get("test_user", {}).get(
            "username", "selftest_admin"
        )
        password = self.ctx._test_password
        if not password:
            logger.error(
                "CRITICAL: _test_password not set by setup - "
                "cannot run auth tests with default/empty credentials"
            )
            return

        # Re-sync password hash and unlock account (the auto-loop may have
        # changed the password or triggered lockout via update endpoints).
        try:
            import src.utils.encryption as _pw_sync_enc

            _pw_db = api.get_db()
            logger.info(
                f"Auth pre-test: db={_pw_db is not None}, test_user_id={self.ctx.test_user_id}, "
                f"password_len={len(password) if password else 0}"
            )
            if _pw_db and self.ctx.test_user_id:
                _pw_db.execute(
                    "UPDATE auth_users SET account_locked = 0, locked_until = NULL, failed_login_attempts = 0 WHERE id = ?",
                    (self.ctx.test_user_id,),
                )
                _new_hash = _pw_sync_enc.hash_password(password)
                logger.info(f"Generated new hash: {_new_hash[:40]}...")
                _pw_db.execute(
                    "UPDATE auth_users SET password_hash = ? WHERE id = ?",
                    (_new_hash, self.ctx.test_user_id),
                )
                # Verify the update took
                _verify = _pw_db.fetch_one(
                    "SELECT password_hash, account_locked, failed_login_attempts FROM auth_users WHERE id = ?",
                    (self.ctx.test_user_id,),
                )
                if _verify:
                    logger.info(
                        f"Auth user after update: hash={str(_verify.get('password_hash', ''))[:40]}..., "
                        f"locked={_verify.get('account_locked', '?')}, "
                        f"failed_attempts={_verify.get('failed_login_attempts', '?')}"
                    )
                else:
                    logger.warning("Auth user row NOT FOUND after password update!")
                logger.info(
                    "Re-synced password hash and unlocked account before standalone auth test"
                )
        except Exception as _pw_e:
            logger.warning(f"Could not re-sync password: {_pw_e}")

        # --- Login (fresh session, no auth) ---
        login_session = self.ctx.requests_module.Session()
        # Use the same internal-secret source as setup.py so the login
        # request is treated identically (is_internal bypasses blacklist etc).
        internal_secret = api.get_internal_secret()
        if internal_secret:
            login_session.headers.update(
                {"X-Plexichat-Internal-Secret": internal_secret}
            )

        logger.info(
            f"Testing POST /api/v1/auth/login (fresh session) with username='{username}', "
            f"password_len={len(password)}, internal_secret={'set' if internal_secret else 'not set'}"
        )
        login_start = time.time()
        resp = login_session.post(
            f"{self.ctx.base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
            timeout=5,
        )
        duration = (time.time() - login_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/auth/login",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "auth_login",
            }
        )
        if success:
            logger.info(f"Auth login PASSED -> {resp.status_code}")
            try:
                token = resp.json().get("token") or resp.json().get("access_token")
                if token:
                    login_session.headers.update({"Authorization": f"Bearer {token}"})
            except Exception:
                pass
        else:
            logger.warning(f"Auth login -> {resp.status_code}: {resp.text[:200]}")

        # --- Register (fresh user via API, tests is_internal bypass) ---
        # Use a non-blacklisted prefix; setup.py blocks ^selftest but internal
        # requests bypass the blacklist. Use a different prefix to be safe.
        rand_user = f"stuser{random.randint(100000, 999999)}"
        rand_email = f"{rand_user}@selftest.plexichat.com"
        register_session = self.ctx.requests_module.Session()
        if internal_secret:
            register_session.headers.update(
                {"X-Plexichat-Internal-Secret": internal_secret}
            )
        logger.info("Testing POST /api/v1/auth/register (fresh user via API)...")
        register_start = time.time()
        resp = register_session.post(
            f"{self.ctx.base_url}/api/v1/auth/register",
            json={"username": rand_user, "email": rand_email, "password": password},
            timeout=10,
        )
        duration = (time.time() - register_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/auth/register",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "auth_register",
            }
        )
        if success:
            logger.info(f"Auth register PASSED -> {resp.status_code}")
        else:
            logger.warning(f"Auth register -> {resp.status_code}: {resp.text[:200]}")

        # --- Sessions list (requires auth) ---
        if login_session.headers.get("Authorization"):
            logger.info("Testing GET /api/v1/auth/sessions...")
            sessions_start = time.time()
            resp = login_session.get(
                f"{self.ctx.base_url}/api/v1/auth/sessions", timeout=5
            )
            duration = (time.time() - sessions_start) * 1000
            success = 200 <= resp.status_code < 300
            self.ctx.results.append(
                {
                    "method": "GET",
                    "path": "/api/v1/auth/sessions",
                    "status_code": resp.status_code,
                    "duration_ms": duration,
                    "success": success,
                    "label": "auth_sessions",
                }
            )
            if success:
                logger.info(f"Auth sessions PASSED -> {resp.status_code}")
            else:
                logger.warning(
                    f"Auth sessions -> {resp.status_code}: {resp.text[:200]}"
                )

            # --- Logout ---
            logger.info("Testing POST /api/v1/auth/logout...")
            logout_start = time.time()
            resp = login_session.post(
                f"{self.ctx.base_url}/api/v1/auth/logout", timeout=5
            )
            duration = (time.time() - logout_start) * 1000
            success = 200 <= resp.status_code < 300
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/auth/logout",
                    "status_code": resp.status_code,
                    "duration_ms": duration,
                    "success": success,
                    "label": "auth_logout",
                }
            )
            if success:
                logger.info(f"Auth logout PASSED -> {resp.status_code}")
            else:
                logger.warning(f"Auth logout -> {resp.status_code}: {resp.text[:200]}")

            # Re-login so we can test revoke-all with a valid session
            time.sleep(0.5)
            logger.debug("Re-logging in for revoke-all test...")
            re_login_resp = login_session.post(
                f"{self.ctx.base_url}/api/v1/auth/login",
                json={"username": username, "password": password},
                timeout=5,
            )
            if re_login_resp.status_code == 200:
                try:
                    new_token = re_login_resp.json().get(
                        "token"
                    ) or re_login_resp.json().get("access_token")
                    if new_token:
                        login_session.headers.update(
                            {"Authorization": f"Bearer {new_token}"}
                        )
                except Exception:
                    pass
            else:
                logger.warning(
                    f"Re-login for revoke-all failed: {re_login_resp.status_code}"
                )

            # --- Sessions revoke-all ---
            # Use except_current=True so the current login_session is preserved.
            # This avoids invalidating the main self-test session (self.ctx.session)
            # which would cause all subsequent tests to fail with 401.
            logger.info("Testing POST /api/v1/auth/sessions/revoke-all...")
            revoke_start = time.time()
            resp = login_session.post(
                f"{self.ctx.base_url}/api/v1/auth/sessions/revoke-all",
                json={"except_current": True},
                timeout=5,
            )
            duration = (time.time() - revoke_start) * 1000
            success = 200 <= resp.status_code < 300
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/auth/sessions/revoke-all",
                    "status_code": resp.status_code,
                    "duration_ms": duration,
                    "success": success,
                    "label": "auth_revoke_all",
                }
            )
            if success:
                logger.info(f"Auth revoke-all PASSED -> {resp.status_code}")
            else:
                logger.warning(
                    f"Auth revoke-all -> {resp.status_code}: {resp.text[:200]}"
                )

        # Re-auth the main session: revoke-all above revoked all OTHER sessions
        # for the same user (including self.ctx.session). We need a fresh token
        # so subsequent standalone tests don't cascade-fail with 401.
        try:
            _reauth_resp = self.ctx.session.post(
                f"{self.ctx.base_url}/api/v1/auth/login",
                json={"username": username, "password": password},
                timeout=5,
            )
            if _reauth_resp.status_code == 200:
                _reauth_json = _reauth_resp.json()
                _new_token = _reauth_json.get("token") or _reauth_json.get(
                    "access_token"
                )
                if _new_token:
                    self.ctx.session.headers.update(
                        {"Authorization": f"Bearer {_new_token}"}
                    )
                    logger.debug("Re-authenticated main session after revoke-all")
        except Exception as _reauth_e:
            logger.warning(f"Failed to re-authenticate main session: {_reauth_e}")
