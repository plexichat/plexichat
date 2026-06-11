"""Feature endpoint tester mixin.

Tests onboarding apply-preset in standalone mode.
"""

import time

import utils.logger as logger

from .base import EndpointTesterBase


class FeatureMixin(EndpointTesterBase):
    """Tests feature-related API endpoints."""

    def test_onboarding_preset(self) -> None:
        """Apply an onboarding preset to the test server."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_server_id:
            logger.debug("Skipping onboarding preset (no server_id)")
            return

        session = self.ctx.session
        server_id = self.ctx.test_server_id

        logger.info(
            "Testing POST /api/v1/features/onboarding/apply-preset (preset=community)..."
        )
        start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/features/onboarding/apply-preset",
            json={"server_id": str(server_id), "preset": "community"},
            timeout=5,
        )
        duration = (time.time() - start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/features/onboarding/apply-preset",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "onboarding_preset",
            }
        )
        if success:
            logger.info(f"Onboarding preset PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Onboarding preset -> {resp.status_code}: {resp.text[:200]}"
            )
