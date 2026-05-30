"""Bot endpoint tester mixin.

Tests bot server integration: request -> approve flow.
"""

import time

import utils.logger as logger

from .base import EndpointTesterBase


class BotMixin(EndpointTesterBase):
    """Tests bot-server integration endpoints."""

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
                "Bot request failed -- skipping approve (no request_id captured)"
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
