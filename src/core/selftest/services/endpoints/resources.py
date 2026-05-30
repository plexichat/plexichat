"""Resource endpoint tester mixin.

Tests DELETE on tracked resources and password-reset confirm flow.
"""

import time
import secrets

import src.api as api
import utils.logger as logger

from .base import EndpointTesterBase


class ResourceMixin(EndpointTesterBase):
    """Tests resource deletion and password-reset endpoints."""

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
