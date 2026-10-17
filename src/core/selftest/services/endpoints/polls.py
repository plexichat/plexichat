"""Poll endpoint tester mixin.

Tests poll vote and close in standalone mode.
"""

import time

import utils.logger as logger

from .base import EndpointTesterBase


class PollMixin(EndpointTesterBase):
    """Tests poll-related API endpoints."""

    def test_poll_vote(self) -> None:
        """Vote on a poll (must run before close).

        The generic auto-loop also exercises POST /{poll_id}/vote and PUT
        /{poll_id}/vote with real option ids, so by the time this standalone
        test runs the test user may already have voted. Be order-aware: when
        a vote already exists, exercise the change-vote (PUT) path with a
        different valid option instead of failing on "already voted".
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_poll_id or not self.ctx.test_poll_option_ids:
            logger.debug("Skipping poll vote (no poll or options)")
            return

        session = self.ctx.session
        poll_id = self.ctx.test_poll_id
        option_ids = self.ctx.test_poll_option_ids

        already_voted = False
        try:
            results_resp = session.get(
                f"{self.ctx.base_url}/api/v1/polls/{poll_id}/results",
                timeout=5,
            )
            if results_resp.status_code == 200:
                already_voted = bool(results_resp.json().get("user_voted", False))
        except Exception as exc:
            logger.debug(f"Poll results pre-check failed (non-fatal): {exc}")

        if already_voted:
            # Test the change-vote endpoint with a different valid option.
            selected_option = option_ids[-1]
            method = "PUT"
            label = "poll_change_vote"
            url = f"{self.ctx.base_url}/api/v1/polls/{poll_id}/vote"
            logger.info(f"Testing PUT /api/v1/polls/{poll_id}/vote (change vote)...")
        else:
            selected_option = option_ids[0]
            method = "POST"
            label = "poll_vote"
            url = f"{self.ctx.base_url}/api/v1/polls/{poll_id}/vote"
            logger.info(f"Testing POST /api/v1/polls/{poll_id}/vote...")

        poll_vote_start = time.time()
        resp = session.request(
            method,
            url,
            json={"option_ids": [int(selected_option)]},
            timeout=5,
        )
        duration = (time.time() - poll_vote_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": method,
                "path": url,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": label,
            }
        )
        if success:
            logger.info(f"{label} PASSED -> {resp.status_code}")
        else:
            logger.warning(f"{label} -> {resp.status_code}: {resp.text[:200]}")

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
