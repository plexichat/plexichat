"""Poll endpoint tester mixin.

Tests poll vote and close in standalone mode.
"""

import time

import utils.logger as logger

from .base import EndpointTesterBase


class PollMixin(EndpointTesterBase):
    """Tests poll-related API endpoints."""

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
