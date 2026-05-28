"""
Test summary report service for SelfTestRunner.

Aggregates results, logs pass/fail statistics, and returns success bool.
"""

import time

import utils.logger as logger

from ..context import SelfTestContext


class ReportGenerator:
    """Generates and logs self-test pass/fail summary."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def report_summary(self) -> bool:
        total = len(self.ctx.results)
        passed = sum(1 for r in self.ctx.results if r["success"])
        failed = total - passed
        duration = time.time() - self.ctx.start_time

        logger.info("=" * 60)
        logger.info("SELF-TEST SUMMARY")
        logger.info(f"Total Endpoints: {total}")
        logger.info(f"Passed:          {passed}")
        logger.info(f"Failed:          {failed}")
        logger.info(
            f"Success Rate:    {(passed / total * 100 if total > 0 else 0):.1f}%"
        )
        logger.info(f"Total Duration:  {duration:.2f}s")
        logger.info("=" * 60)

        if failed > 0:
            logger.error("Failed Endpoints (Non-2xx Responses):")
            for r in self.ctx.results:
                if not r["success"]:
                    logger.error(
                        f"  - {r['method']:<6} {r['path']} (Status: {r['status_code']})"
                    )
                    if "error" in r:
                        logger.error(f"    Error: {r['error']}")
            logger.error("See detailed logs above or in latest.log")

        return failed == 0
