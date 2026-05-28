"""
Endpoint test execution service for SelfTestRunner.

Executes individual API endpoint tests, DELETE resource tests,
and specialised bot-server integration flows.
"""

import time
import secrets
import random
import re
from typing import Any, Dict

import src.api as api
import utils.logger as logger

from ..context import SelfTestContext


class EndpointTester:
    """Executes single-endpoint tests, DELETE suite, and bot integration."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def test_endpoint(
        self,
        method: str,
        path: str,
        route_details: Dict[str, Any],
        use_other: bool = False,
    ) -> None:
        url_path = path
        query_params = {}
        active_session = self.ctx.other_session if use_other else self.ctx.session

        for param in route_details.get("parameters", []):
            p_name = param.get("name")
            val = self.ctx.data.get_param_value(p_name, path)

            logger.info(f"  Param: {p_name}={val} (in {param.get('in')})")

            if param.get("in") == "path":
                url_path = url_path.replace(f"{{{p_name}}}", val)
                url_path = url_path.replace(f"{{{p_name.lower()}}}", val)
            elif param.get("in") == "query":
                query_params[p_name] = val

        if "delay-deletion" in path and method == "POST":
            future_deletion_at = int(time.time()) + 86400
            query_params["deletion_at"] = str(future_deletion_at)

        if "/users/search" in path and method == "GET":
            query_params["username"] = self.ctx.config.get("test_user", {}).get(
                "username", "selftest_admin"
            )

        if (
            "/channels/invites/" in path or "/servers/invites/" in path
        ) and method == "POST":
            if not use_other and self.ctx.other_session:
                active_session = self.ctx.other_session

        # Relationship POST (send friend request) should use OTHER session
        # since setup sends the request from other_user -> admin user
        if (
            not use_other
            and self.ctx.other_token
            and "/relationships" in path
            and method == "POST"
        ):
            active_session = self.ctx.other_session

        json_body = None
        files = None
        form_data = {}

        request_body = route_details.get("request_body", {})
        content_types = request_body.get("content", {})

        if not content_types or (
            "multipart/form-data" not in content_types
            and "application/x-www-form-urlencoded" not in content_types
        ):
            form_params = [
                p
                for p in route_details.get("parameters", [])
                if p.get("in") in ("formData", "form")
            ]
            if form_params:
                props = {}
                for p in form_params:
                    p_schema = p.get("schema", {})
                    if not p_schema:
                        p_schema = {"type": "string"}
                    props[p["name"]] = p_schema
                content_types["multipart/form-data"] = {
                    "schema": {
                        "properties": props,
                        "required": [
                            p["name"] for p in form_params if p.get("required")
                        ],
                    }
                }

        if "multipart/form-data" in content_types:
            if "voice-messages/upload" in path:
                ogg_header = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
                files = {
                    "audio": (
                        "voice_test.ogg",
                        ogg_header + b"\x00" * 1024,
                        "audio/ogg",
                    )
                }
                form_data["conversation_id"] = str(
                    self.ctx.test_conversation_id or self.ctx.test_channel_id or 1
                )
                form_data["duration_ms"] = "5000"
            else:
                if "/stickers" in path or "/emojis" in path:
                    file_field = "image"
                else:
                    file_field = "file"
                from PIL import Image
                import io as _io

                _img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
                _buf = _io.BytesIO()
                _img.save(_buf, format="PNG")
                png = _buf.getvalue()
                files = {
                    file_field: (
                        "test_file.png",
                        png,
                        "image/png",
                    )
                }

                schema = content_types["multipart/form-data"].get("schema", {})
                props = schema.get("properties", {})
                for p_name, p_schema in props.items():
                    if p_name != file_field:
                        form_data[p_name] = self.ctx.data.generate_from_schema(
                            p_schema, p_name
                        )

                if "/stickers" in path or "/emojis" in path:
                    name_val = form_data.get("name", "")
                    if not name_val or len(str(name_val)) < 2:
                        form_data["name"] = "test_asset_" + secrets.token_hex(4)
                    else:
                        form_data["name"] = re.sub(
                            r"[^a-z0-9_]", "_", str(form_data["name"]).lower()
                        )
                        if len(form_data["name"]) < 2:
                            form_data["name"] = "test_asset_" + secrets.token_hex(4)
                if "/stickers/" in path and "/send" in path and method == "POST":
                    form_data["message_id"] = str(
                        self.ctx.test_message_id or self.ctx.data.generate_snowflake()
                    )
        elif "application/x-www-form-urlencoded" in content_types:
            schema = content_types["application/x-www-form-urlencoded"].get(
                "schema", {}
            )
            props = schema.get("properties", {})
            for p_name, p_schema in props.items():
                form_data[p_name] = self.ctx.data.generate_from_schema(p_schema, p_name)
            if "/stickers/" in path and "/send" in path and method == "POST":
                if "message_id" not in form_data or not form_data.get("message_id"):
                    form_data["message_id"] = str(
                        self.ctx.test_message_id or self.ctx.data.generate_snowflake()
                    )
        elif method in ("POST", "PUT", "PATCH"):
            json_body = self.ctx.data.get_minimal_body(request_body, path, method)

        if (
            json_body
            and ("/emojis" in path or "/stickers" in path)
            and "search" not in path
        ):
            if "name" not in json_body or not json_body.get("name"):
                json_body["name"] = "test_asset_" + secrets.token_hex(4)
            elif isinstance(json_body.get("name"), str):
                json_body["name"] = re.sub(
                    r"[^a-z0-9_]", "_", json_body["name"].lower()
                )

        start = time.time()
        try:
            resp = active_session.request(
                method,
                f"{self.ctx.base_url}{url_path}",
                json=json_body,
                data=form_data if form_data else None,
                files=files,
                params=query_params,
                timeout=5,
            )
            duration = (time.time() - start) * 1000

            success = 200 <= resp.status_code < 300
            if not success and "/api/v1/admin/plexijoin/" in path:
                expected = self.ctx.discovery.plexijoin_expected_statuses()
                if resp.status_code in expected:
                    success = True

            traceback_data = None
            if not success and self.ctx.config.get("retry_on_failure", True):
                retry_resp = active_session.request(
                    method,
                    f"{self.ctx.base_url}{url_path}",
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

            result = {
                "method": method,
                "path": path,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "traceback": traceback_data,
            }
            self.ctx.results.append(result)

            # Capture webhook ID from auto-loop POST /webhooks so subsequent
            # PATCH /webhooks/{id} and POST execute tests use the right ID.
            # Always capture (even if setup left a stale value) so the latest
            # webhook ID/token are used throughout the test suite.
            if (
                success
                and method == "POST"
                and "/webhooks" in path
                and "/webhook." not in path
            ):
                try:
                    webhook_data = resp.json()
                    if isinstance(webhook_data, dict):
                        wid = webhook_data.get("id")
                        if wid:
                            self.ctx.test_webhook_id = int(wid)
                            self.ctx.test_webhook_token = webhook_data.get("token")
                            logger.debug(
                                f"Captured webhook id={wid} from auto-loop POST"
                            )
                except Exception:
                    pass

            if not success:
                logger.error(
                    f"FAILED: {method:<6} {path:<40} -> Status {resp.status_code} ({duration:.1f}ms)"
                )
                if resp.status_code == 400:
                    logger.error(f"  Validation Error: {resp.text[:200]}")
                if traceback_data:
                    logger.error(f"Captured Traceback for {path}:\n{traceback_data}")
            elif self.ctx.config.get("verbose", False):
                logger.info(
                    f"PASSED: {method:<6} {path:<40} -> Status {resp.status_code} ({duration:.1f}ms)"
                )

        except Exception as e:
            self.ctx.results.append(
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

    def test_delete_resources(self) -> None:
        logger.info("Testing DELETE endpoints for tracked resources...")

        delete_tests = []

        if self.ctx.test_message_id and self.ctx.test_channel_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/channels/{self.ctx.test_channel_id}/messages/{self.ctx.test_message_id}",
                    "message",
                )
            )

        if self.ctx.test_webhook_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/webhooks/{self.ctx.test_webhook_id}",
                    "webhook",
                )
            )

        if self.ctx.test_channel_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/channels/{self.ctx.test_channel_id}",
                    "channel",
                )
            )

        if self.ctx.test_invite_code:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/channels/invites/{self.ctx.test_invite_code}",
                    "invite",
                )
            )

        if self.ctx.test_role_id and self.ctx.test_server_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/servers/{self.ctx.test_server_id}/roles/{self.ctx.test_role_id}",
                    "role",
                )
            )

        if self.ctx.test_server_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/servers/{self.ctx.test_server_id}",
                    "server",
                )
            )

        if self.ctx.test_application_id:
            delete_tests.append(
                (
                    "DELETE",
                    f"/api/v1/applications/{self.ctx.test_application_id}",
                    "application",
                )
            )

        for method, path, label in delete_tests:
            start = time.time()
            try:
                resp = self.ctx.session.request(
                    method, f"{self.ctx.base_url}{path}", timeout=5
                )
                duration = (time.time() - start) * 1000
                success = 200 <= resp.status_code < 300
                self.ctx.results.append(
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
                self.ctx.results.append(
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

            if success:
                if label == "delete_message":
                    self.ctx.test_message_id = None
                elif label == "delete_webhook":
                    self.ctx.test_webhook_id = None
                elif label == "delete_channel":
                    self.ctx.test_channel_id = None
                    self.ctx.test_conversation_id = None
                elif label == "delete_invite":
                    self.ctx.test_invite_code = None
                elif label == "delete_role":
                    self.ctx.test_role_id = None
                elif label == "delete_server":
                    self.ctx.test_server_id = None
                elif label == "delete_application":
                    self.ctx.test_application_id = None

            time.sleep(0.05)

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

        time.sleep(1.5)
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
        time.sleep(2.0)
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
            time.sleep(0.3)
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
            time.sleep(0.3)
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
            time.sleep(0.3)
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

    def test_migration_endpoints(self) -> None:
        """Test migration apply and rollback using the 037 selftest no-op migration."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping migration tests (no auth token)")
            return

        session = self.ctx.session
        mig_path_apply = "/api/v1/admin/database/migrations/apply/037"
        mig_path_rollback = "/api/v1/admin/database/migrations/rollback/037"

        # Migration 037 may already be applied during server startup.
        # Rollback first so we can test apply; then rollback again to leave
        # the DB in the same state we found it.
        logger.info(
            "Testing POST /api/v1/admin/database/migrations/rollback/037 (pre-test)..."
        )
        rb1_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_path_rollback}",
            timeout=10,
        )
        rb1_ok = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_path_rollback,
                "status_code": resp.status_code,
                "duration_ms": (time.time() - rb1_start) * 1000,
                "success": rb1_ok,
                "label": "migration_rollback_pre",
            }
        )
        if rb1_ok:
            logger.info(
                f"Migration rollback 037 (pre-test) PASSED -> {resp.status_code}"
            )
        else:
            logger.debug(
                f"Migration rollback 037 (pre-test) -> {resp.status_code}: {resp.text[:200]}"
            )

        time.sleep(0.05)

        # --- Apply migration 037 ---
        logger.info("Testing POST /api/v1/admin/database/migrations/apply/037...")
        mig_apply_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_path_apply}",
            timeout=10,
        )
        duration = (time.time() - mig_apply_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_path_apply,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "migration_apply",
            }
        )
        if success:
            logger.info(f"Migration apply 037 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration apply 037 -> {resp.status_code}: {resp.text[:200]}"
            )

        time.sleep(0.05)

        # --- Rollback migration 037 ---
        logger.info("Testing POST /api/v1/admin/database/migrations/rollback/037...")
        mig_rollback_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_path_rollback}",
            timeout=10,
        )
        duration = (time.time() - mig_rollback_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_path_rollback,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "migration_rollback",
            }
        )
        if success:
            logger.info(f"Migration rollback 037 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration rollback 037 -> {resp.status_code}: {resp.text[:200]}"
            )

    def test_access_token_rotate(self) -> None:
        """Test access token create → rotate → delete cycle."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_access_token_id:
            logger.debug("Skipping access token rotate (no token_id)")
            return

        session = self.ctx.session
        token_id = self.ctx.test_access_token_id

        # --- Rotate ---
        logger.info(
            f"Testing POST /api/v1/admin/security/access-tokens/{token_id}/rotate..."
        )
        rotate_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/admin/security/access-tokens/{token_id}/rotate",
            json={"token": secrets.token_urlsafe(48)},
            timeout=5,
        )
        duration = (time.time() - rotate_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/admin/security/access-tokens/{token_id}/rotate",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "access_token_rotate",
            }
        )
        rotated_token_id = token_id
        if success:
            logger.info(f"Access token rotate PASSED -> {resp.status_code}")
            try:
                rotate_data = resp.json()
                # The rotate endpoint nests the new token under "access_token".
                # Capture the new id so we revoke the fresh token, not the old one.
                new_id = (
                    rotate_data.get("access_token", {}).get("id")
                    or rotate_data.get("id")
                    or rotate_data.get("token_id")
                )
                if new_id:
                    rotated_token_id = int(new_id)
                    logger.debug(f"Rotate returned new token id: {rotated_token_id}")
            except Exception:
                pass
        else:
            logger.warning(
                f"Access token rotate -> {resp.status_code}: {resp.text[:200]}"
            )

        time.sleep(0.05)

        # --- Revoke ---
        # The admin API route is POST /security/access-tokens/{token_id}/revoke
        # (defined in admin/security.py).
        logger.info(
            f"Testing POST /api/v1/admin/security/access-tokens/{rotated_token_id}/revoke..."
        )
        revoke_token_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/admin/security/access-tokens/{rotated_token_id}/revoke",
            timeout=5,
        )
        duration = (time.time() - revoke_token_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/admin/security/access-tokens/{rotated_token_id}/revoke",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "access_token_revoke",
            }
        )
        if success:
            logger.info(f"Access token revoke PASSED -> {resp.status_code}")
            self.ctx.test_access_token_id = None
        else:
            logger.warning(
                f"Access token revoke -> {resp.status_code}: {resp.text[:200]}"
            )

    def test_delay_deletion(self) -> None:
        """Test delay-deletion using the API-created other_user."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_other_user_id:
            logger.debug("Skipping delay-deletion (no other_user_id)")
            return

        session = self.ctx.session
        other_id = self.ctx.test_other_user_id

        # Delete the user via API first
        logger.info("Testing DELETE /api/v1/users/@me (other_user)...")
        other_session = self.ctx.other_session
        if other_session and other_session.headers.get("Authorization"):
            resp = other_session.delete(
                f"{self.ctx.base_url}/api/v1/users/@me",
                json={"password": self.ctx._test_password or "SelfTest_Generated_123!"},
                timeout=5,
            )
            success = resp.status_code in (200, 204)
            self.ctx.results.append(
                {
                    "method": "DELETE",
                    "path": "/api/v1/users/@me",
                    "status_code": resp.status_code,
                    "duration_ms": 0,
                    "success": success,
                    "label": "other_user_self_delete",
                }
            )
            if success:
                logger.info(f"Other user delete PASSED -> {resp.status_code}")
            else:
                logger.warning(
                    f"Other user delete -> {resp.status_code}: {resp.text[:200]}"
                )

        time.sleep(0.05)

        # --- Delay deletion ---
        future_deletion_at = int(time.time()) + 86400
        logger.info(f"Testing POST /api/v1/admin/users/{other_id}/delay-deletion...")
        delay_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/admin/users/{other_id}/delay-deletion",
            params={"deletion_at": str(future_deletion_at)},
            timeout=5,
        )
        duration = (time.time() - delay_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/admin/users/{other_id}/delay-deletion",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "delay_deletion",
            }
        )
        if success:
            logger.info(f"Delay deletion PASSED -> {resp.status_code}")
        else:
            logger.warning(f"Delay deletion -> {resp.status_code}: {resp.text[:200]}")

    def test_password_reset_confirm(self) -> None:
        """Test password-reset/confirm for the API-created other_user."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_other_user_id:
            logger.debug("Skipping password-reset confirm (no other_user_id)")
            return

        session = self.ctx.session

        # First, create a password reset request for the other user
        auth_mod = api.get_auth()
        if not auth_mod:
            logger.debug("Skipping password reset confirm (auth module unavailable)")
            return

        db = api.get_db()
        if not db:
            logger.debug("Skipping password reset confirm (db unavailable)")
            return

        # Get other user's email_index (the blind-index column used for lookups)
        other_email = None
        try:
            other_row = db.fetch_one(
                "SELECT email_index FROM auth_users WHERE id = ?",
                (self.ctx.test_other_user_id,),
            )
            if other_row:
                other_email = (
                    other_row.get("email_index")
                    if isinstance(other_row, dict)
                    else other_row[0]
                )
        except Exception:
            pass

        if not other_email:
            logger.warning(
                "Password reset confirm skipped: could not find other user email_index for id=%s",
                self.ctx.test_other_user_id,
            )
            return

        # Check if password_reset_tokens table exists before inserting
        table_exists = False
        try:
            tbl = db.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='password_reset_tokens'"
            )
            table_exists = tbl is not None
        except Exception:
            pass

        if not table_exists:
            logger.debug(
                "Password reset confirm skipped: password_reset_tokens table does not exist"
            )
            return

        # Create a reset token directly in DB
        reset_token = secrets.token_urlsafe(32)
        try:
            db.execute(
                "INSERT OR REPLACE INTO password_reset_tokens (token, user_id, created_at, expires_at, used) VALUES (?, ?, ?, ?, 0)",
                (
                    reset_token,
                    self.ctx.test_other_user_id,
                    int(time.time()),
                    int(time.time()) + 3600,
                ),
            )
        except Exception as e:
            logger.warning(
                "Password reset confirm skipped: could not create reset token in DB: %s. "
                "This may indicate the password_reset_tokens table schema differs.",
                e,
            )
            return

        # --- Password reset confirm ---
        new_password = self.ctx._test_password or "SelfTest_Generated_123!"
        logger.info("Testing POST /api/v1/auth/password-reset/confirm...")
        password_reset_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/auth/password-reset/confirm",
            json={"token": reset_token, "new_password": new_password},
            timeout=5,
        )
        duration = (time.time() - password_reset_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/auth/password-reset/confirm",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "password_reset_confirm",
            }
        )
        if success:
            logger.info(f"Password reset confirm PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Password reset confirm -> {resp.status_code}: {resp.text[:200]}"
            )

    def test_media_upload_complete(self) -> None:
        """Test chunked media upload: create session → upload chunk → complete."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_server_id:
            logger.debug("Skipping media upload complete (no server_id)")
            return

        session = self.ctx.session

        # Generate a small random text file in memory
        file_content = secrets.token_hex(64).encode("utf-8")  # 128 bytes of random hex
        filename = f"selftest_{secrets.token_hex(4)}.txt"
        content_type = "text/plain"
        file_size = len(file_content)

        # Step 1: Create upload session
        logger.info("Creating test upload session...")
        create_resp = session.post(
            f"{self.ctx.base_url}/api/v1/media/upload/session",
            json={
                "filename": filename,
                "content_type": content_type,
                "total_size": file_size,
            },
            timeout=5,
        )
        session_id = None
        if create_resp.status_code in (200, 201):
            try:
                session_id = create_resp.json().get("session_id")
            except Exception:
                pass

        if not session_id:
            logger.debug(
                "Skipping media upload complete (could not create upload session)"
            )
            return

        # Step 2: Upload the file as a single chunk
        logger.info(f"Uploading chunk 0 for session {session_id}...")
        chunk_resp = session.post(
            f"{self.ctx.base_url}/api/v1/media/upload/chunk/{session_id}",
            params={"chunk_index": 0},
            files={"file": (filename, file_content, content_type)},
            timeout=15,
        )
        chunk_ok = 200 <= chunk_resp.status_code < 300
        if not chunk_ok:
            logger.warning(
                f"Chunk upload returned {chunk_resp.status_code}: {chunk_resp.text[:200]}"
            )
            # Still try to complete to see what happens

        # Step 3: Complete the upload session
        logger.info(f"Completing upload session {session_id}...")
        complete_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/media/upload/complete/{session_id}",
            timeout=15,
        )
        duration = (time.time() - complete_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/media/upload/complete/{session_id}",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "media_upload_complete",
                "chunk_uploaded": chunk_ok,
                "file_size": file_size,
            }
        )
        if success:
            logger.info(
                f"Media upload complete PASSED -> {resp.status_code} "
                f"(chunk_ok={chunk_ok}, size={file_size}B, {duration:.1f}ms)"
            )
        else:
            logger.warning(
                f"Media upload complete -> {resp.status_code}: {resp.text[:200]}"
            )

    def test_poll_vote(self) -> None:
        """Vote on a poll (must run before close)."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_poll_id or not self.ctx.test_poll_option_ids:
            logger.debug("Skipping poll vote (no poll or options)")
            return

        session = self.ctx.session
        poll_id = self.ctx.test_poll_id
        option_ids = self.ctx.test_poll_option_ids

        logger.info(f"Testing POST /api/v1/polls/{poll_id}/vote...")
        poll_vote_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/polls/{poll_id}/vote",
            json={"option_ids": [int(oid) for oid in option_ids]},
            timeout=5,
        )
        duration = (time.time() - poll_vote_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/polls/{poll_id}/vote",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "poll_vote",
            }
        )
        if success:
            logger.info(f"Poll vote PASSED -> {resp.status_code}")
        else:
            logger.warning(f"Poll vote -> {resp.status_code}: {resp.text[:200]}")

    def test_poll_close(self) -> None:
        """Close a poll (runs after vote)."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_poll_id:
            logger.debug("Skipping poll close (no poll_id)")
            return

        session = self.ctx.session
        poll_id = self.ctx.test_poll_id

        logger.info(f"Testing POST /api/v1/polls/{poll_id}/close...")
        poll_close_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/polls/{poll_id}/close",
            timeout=5,
        )
        duration = (time.time() - poll_close_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/polls/{poll_id}/close",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "poll_close",
            }
        )
        if success:
            logger.info(f"Poll close PASSED -> {resp.status_code}")
        else:
            logger.warning(f"Poll close -> {resp.status_code}: {resp.text[:200]}")

    def test_bot_server_integration(self) -> None:
        if not self.ctx.test_server_id or not self.ctx.test_application_id:
            logger.debug(
                "Skipping bot server integration test (missing server or application)"
            )
            return

        # Check if bot is already approved (by auto-loop)
        check_resp = self.ctx.session.get(
            f"{self.ctx.base_url}/api/v1/bots/servers/{self.ctx.test_server_id}/approved",
            timeout=5,
        )
        if check_resp.status_code == 200:
            try:
                approved_bots = check_resp.json()
                if isinstance(approved_bots, list):
                    for bot in approved_bots:
                        if (
                            isinstance(bot, dict)
                            and bot.get("application_id")
                            == self.ctx.test_application_id
                        ):
                            logger.info(
                                "Bot already approved by auto-loop, skipping test"
                            )
                            return
            except Exception:
                pass

        logger.info("Testing bot server integration (request -> approve)...")

        req_body = {"application_id": int(self.ctx.test_application_id)}
        req_path = f"/api/v1/bots/servers/{self.ctx.test_server_id}/request"
        start = time.time()
        request_succeeded = False
        try:
            resp = self.ctx.session.post(
                f"{self.ctx.base_url}{req_path}",
                json=req_body,
                timeout=5,
            )
            duration = (time.time() - start) * 1000
            request_succeeded = 200 <= resp.status_code < 300
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": req_path,
                    "status_code": resp.status_code,
                    "duration_ms": duration,
                    "success": request_succeeded,
                    "label": "bot_request",
                }
            )
            if request_succeeded:
                try:
                    req_data = resp.json()
                    req_id = req_data.get("id") or req_data.get("request_id")
                    if req_id:
                        self.ctx.test_bot_request_id = int(req_id)
                        logger.debug(
                            f"Captured bot request ID: {self.ctx.test_bot_request_id}"
                        )
                except Exception:
                    pass
                logger.info(
                    f"Bot request PASSED -> {resp.status_code} ({duration:.1f}ms)"
                )
            else:
                logger.warning(f"Bot request -> {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            self.ctx.results.append(
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

        # Only attempt approve if request succeeded (captured request_id)
        if not request_succeeded and not self.ctx.test_bot_request_id:
            logger.warning(
                "Bot request failed — skipping approve (no request_id captured)"
            )
            return

        # Use PUT review endpoint to approve (avoids 405 on POST /approve route)
        request_id = self.ctx.test_bot_request_id
        review_path = (
            f"/api/v1/bots/servers/{self.ctx.test_server_id}/requests/{request_id}"
        )
        review_body = {"approve": True}
        start = time.time()
        try:
            resp = self.ctx.session.put(
                f"{self.ctx.base_url}{review_path}",
                json=review_body,
                timeout=5,
            )
            duration = (time.time() - start) * 1000
            success = 200 <= resp.status_code < 300
            self.ctx.results.append(
                {
                    "method": "PUT",
                    "path": review_path,
                    "status_code": resp.status_code,
                    "duration_ms": duration,
                    "success": success,
                    "label": "bot_review",
                }
            )
            if success:
                logger.info(
                    f"Bot review approve PASSED -> {resp.status_code} ({duration:.1f}ms)"
                )
            else:
                logger.warning(
                    f"Bot review approve -> {resp.status_code}: {resp.text[:200]}"
                )
        except Exception as e:
            self.ctx.results.append(
                {
                    "method": "PUT",
                    "path": review_path,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "error": str(e),
                    "label": "bot_review",
                }
            )
            logger.error(f"Bot review approve EXCEPTION: {e}")
