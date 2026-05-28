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

    def _get_source(self, r: dict) -> str:
        if r.get("label") and r["label"] not in ("auto_login",):
            known_standalone_labels = {
                "auth_login",
                "auth_logout",
                "auth_register",
                "auth_sessions",
                "auth_revoke_all",
                "bot_request",
                "bot_approve",
                "media_upload_complete",
                "poll_vote",
                "poll_close",
                "access_token_create",
                "access_token_rotate",
                "access_token_revoke",
                "delay_deletion",
                "password_reset",
                "password_reset_confirm",
                "ratelimit",
                "migration",
            }
            label = r.get("label", "")
            if label.startswith("delete_") or label.startswith("bot_"):
                return "delete_batch"
            if label in known_standalone_labels or "_" in label:
                return "standalone"
            return "auto_loop"
        return "auto_loop"

    def report_summary(self) -> bool:
        total = len(self.ctx.results)
        passed = sum(1 for r in self.ctx.results if r["success"])
        failed = total - passed
        duration = time.time() - self.ctx.start_time

        by_source: dict[str, dict] = {}
        for r in self.ctx.results:
            src = self._get_source(r)
            if src not in by_source:
                by_source[src] = {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "duration_ms": 0,
                }
            by_source[src]["total"] += 1
            if r["success"]:
                by_source[src]["passed"] += 1
            else:
                by_source[src]["failed"] += 1
            by_source[src]["duration_ms"] += r.get("duration_ms", 0)

        with_duration = [
            (r, r.get("duration_ms", 0))
            for r in self.ctx.results
            if r.get("duration_ms", 0) > 0
        ]
        with_duration.sort(key=lambda x: x[1], reverse=True)
        slowest = with_duration[:10]

        logger.info("=" * 60)
        logger.info("SELF-TEST SUMMARY")
        logger.info(f"Total Endpoints: {total}")
        logger.info(f"Passed:          {passed}")
        logger.info(f"Failed:          {failed}")
        logger.info(
            f"Success Rate:    {(passed / total * 100 if total > 0 else 0):.1f}%"
        )
        logger.info(f"Total Duration:  {duration:.2f}s")
        logger.info("-" * 60)
        logger.info("BREAKDOWN BY SOURCE:")
        for src in ["auto_loop", "standalone", "delete_batch"]:
            if src in by_source:
                s = by_source[src]
                rate = (s["passed"] / s["total"] * 100) if s["total"] > 0 else 0
                logger.info(
                    f"  {src:<15} {s['total']:>4} total | {s['passed']:>4} ok | {s['failed']:>4} fail | {rate:>5.1f}% | {s['duration_ms']:>8.1f}ms"
                )
        logger.info("-" * 60)
        logger.info("SLOWEST ENDPOINTS:")
        for r, dur in slowest:
            status = r.get("status_code", 0)
            success = "OK" if r["success"] else "FAIL"
            label = r.get("label", r.get("path", ""))
            logger.info(
                f"  {dur:>8.1f}ms | {success:<4} | {r['method']:<6} {label[:45]:<45} ({status})"
            )
        logger.info("=" * 60)

        if failed > 0:
            logger.error("Failed Endpoints (Non-2xx Responses):")
            for r in self.ctx.results:
                if not r["success"]:
                    label = r.get("label", r.get("path", ""))
                    logger.error(
                        f"  - {r['method']:<6} {label} (Status: {r['status_code']})"
                    )
                    if "error" in r:
                        logger.error(f"    Error: {r['error']}")
            logger.error("See detailed logs above or in latest.log")

        return failed == 0
