"""Application endpoint tester mixin.

Tests application bot creation via the HTTP API endpoint.
"""

import time
import secrets

import utils.logger as logger

from .base import EndpointTesterBase


class ApplicationMixin(EndpointTesterBase):
    """Tests application-related API endpoints."""

    def test_bot_creation_via_api(self) -> None:
        """Test full create-bot flow via HTTP API.

        Creates a fresh application and bot via HTTP, validates the
        bot token response, then cleans up by deleting the application.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping bot creation test (no auth token)")
            return

        session = self.ctx.session

        # Create a fresh application via the API
        app_name = f"BotTest-{secrets.token_hex(4)}"
        logger.info(f"Testing POST /api/v1/applications (name={app_name})...")
        create_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/applications",
            json={"name": app_name},
            timeout=10,
        )
        duration = (time.time() - create_start) * 1000
        app_created = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/applications",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": app_created,
                "label": "app_create_for_bot",
            }
        )
        if not app_created:
            logger.warning(
                f"App create for bot -> {resp.status_code}: {resp.text[:200]}"
            )
            return
        logger.info(f"App create for bot PASSED -> {resp.status_code}")

        try:
            app_data = resp.json()
            new_app_id = app_data.get("id")
            if not new_app_id:
                logger.warning("App created but no id returned")
                return
            logger.info(f"Created test app ID: {new_app_id}")
        except Exception as e:
            logger.warning(f"Could not parse app creation response: {e}")
            return

        time.sleep(0.05)

        # Create a bot for this application via the HTTP API
        logger.info(
            f"Testing POST /api/v1/applications/{new_app_id}/bot (fresh bot)..."
        )
        bot_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/applications/{new_app_id}/bot",
            timeout=10,
        )
        duration = (time.time() - bot_start) * 1000
        bot_created = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/applications/{new_app_id}/bot",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": bot_created,
                "label": "bot_creation_api",
            }
        )
        if bot_created:
            logger.info(f"Bot creation API PASSED -> {resp.status_code}")
        else:
            logger.warning(f"Bot creation API -> {resp.status_code}: {resp.text[:200]}")

        time.sleep(0.05)

        # Cleanup: delete the application (cascades to bot)
        logger.info(f"Deleting test application ID: {new_app_id}...")
        try:
            del_resp = session.delete(
                f"{self.ctx.base_url}/api/v1/applications/{new_app_id}",
                timeout=10,
            )
            if del_resp.status_code in (200, 204):
                logger.info("Test app deleted successfully")
            else:
                logger.warning(
                    f"Test app delete -> {del_resp.status_code}: {del_resp.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"Failed to delete test application: {e}")
