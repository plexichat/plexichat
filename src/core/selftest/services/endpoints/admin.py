"""Admin endpoint tester mixin.

Tests migration apply/rollback, access token rotate/revoke,
and delay-deletion in standalone mode.
"""

import time
import secrets

import utils.logger as logger

from .base import EndpointTesterBase


class AdminMixin(EndpointTesterBase):
    """Tests admin-related API endpoints."""

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
        """Test access token create -> rotate -> delete cycle."""
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
                json={"password": self.ctx._test_password},
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
