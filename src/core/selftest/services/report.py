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
        # Skipped entries are recorded honestly as skipped: they are neither
        # passes (inflating confidence) nor server faults.
        skipped = sum(1 for r in self.ctx.results if r.get("skipped"))
        passed = sum(
            1 for r in self.ctx.results if r["success"] and not r.get("skipped")
        )
        failed = total - passed - skipped
        duration = time.time() - self.ctx.start_time

        by_source: dict[str, dict] = {}
        for r in self.ctx.results:
            src = self._get_source(r)
            if src not in by_source:
                by_source[src] = {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "duration_ms": 0,
                }
            by_source[src]["total"] += 1
            if r["success"] and not r.get("skipped"):
                by_source[src]["passed"] += 1
            elif r.get("skipped"):
                by_source[src]["skipped"] += 1
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
        if skipped:
            logger.info(f"Skipped:         {skipped}")
        logger.info(
            f"Success Rate:    {(passed / total * 100 if total > 0 else 0):.1f}%"
        )
        logger.info(f"Total Duration:  {duration:.2f}s")
        logger.info("-" * 60)
        logger.info("HARNESS ASSUMPTIONS (active during this run):")
        if getattr(self.ctx, "status_only", False):
            logger.info("  Status mode: no test allowances active. All checks")
            logger.info("  unauthenticated through production middleware; nothing")
            logger.info("  was created, mutated, bypassed, or mocked.")
        else:
            logger.info("  Read 100% against these caveats, not as production proof:")
            for line in (
                "- rate limiting bypassed for localhost + internal-secret requests",
                "- DSAR 1/day throttle bypassed for the same self-test requests",
                "- federation delivery to loopback https peers allowed "
                "(PLEXICHAT_SELF_TEST URLValidator allowance; prod needs public DNS)",
                "- federation delivery verified against a fake peer: throwaway CA, "
                "trust published via SSL_CERT_FILE process-scoped, restored after",
                "- OAuth login-init uses shadowed test client_ids (in-process "
                "config shadow; config files untouched; callback untestable offline)",
                "- suite seeds its own DB rows (inbound requests, connections, "
                "OAuth states, emoji/slowmode/poll fixtures) and deletes them after",
                "- TOTP steps use previous-window codes (no sleeps); auth/session "
                "expiry races are unit-tested, not suite-tested",
            ):
                logger.info(f"  {line}")
        logger.info("-" * 60)
        logger.info("BREAKDOWN BY SOURCE:")
        for src in ["auto_loop", "standalone", "delete_batch"]:
            if src in by_source:
                s = by_source[src]
                judged = s["passed"] + s["failed"]
                rate = (s["passed"] / judged * 100) if judged > 0 else 0
                logger.info(
                    f"  {src:<15} {s['total']:>4} total | {s['passed']:>4} ok | "
                    f"{s['failed']:>4} fail | {s['skipped']:>4} skip | {rate:>5.1f}% | "
                    f"{s['duration_ms']:>8.1f}ms"
                )
        logger.info("-" * 60)
        logger.info("SLOWEST ENDPOINTS:")
        for r, dur in slowest:
            status = r.get("status_code", 0)
            success = "OK" if r["success"] else "FAIL"
            label = r.get("label", r.get("path", ""))
            expected = (
                f" expected={r['expected_denial']}" if r.get("expected_denial") else ""
            )
            logger.info(
                f"  {dur:>8.1f}ms | {success:<4} | {r['method']:<6} {label[:45]:<45} ({status}){expected}"
            )

        ram = self.ctx.ram_monitor
        if ram is not None:
            ram.stop()
            summary = ram.summary()
            logger.info("-" * 60)
            logger.info("RAM USAGE SUMMARY:")
            if summary:
                logger.info(
                    f"  Process RSS:  start {summary['start_rss_mb']:>8.1f} MB | "
                    f"end {summary['end_rss_mb']:>8.1f} MB | "
                    f"peak {summary['peak_rss_mb']:>8.1f} MB | "
                    f"delta {summary['delta_rss_mb']:+.1f} MB"
                )
                logger.info(
                    f"  System Memory: used {summary['system_used_mb']:>9.1f} MB / "
                    f"{summary['system_total_mb']:>9.1f} MB "
                    f"({summary['system_percent']:.1f}%)"
                )
            else:
                logger.info("  (no RAM samples collected)")

        logger.info("=" * 60)

        if failed > 0:
            logger.error("Failed endpoint checks:")
            for r in self.ctx.results:
                if not r["success"] and not r.get("skipped"):
                    label = r.get("label", r.get("path", ""))
                    logger.error(
                        f"  - {r['method']:<6} {label} (Status: {r['status_code']})"
                    )
                    if "error" in r:
                        logger.error(f"    Error: {r['error']}")
            logger.error("See detailed logs above or in latest.log")

        return failed == 0
