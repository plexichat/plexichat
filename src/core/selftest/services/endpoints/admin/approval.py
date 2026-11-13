import time

import utils.logger as logger

from .base import AdminMixinBase


class ApprovalMixin(AdminMixinBase):
    """Approval-gated destructive action coverage."""

    def test_approval_gated_actions(self) -> None:
        """Execute the remaining approval-gated endpoints with real approvals.

        Each gated action consumes a single-use approval, so the ceremony
        (request + 2 approvals) runs per action. Safe variants are used
        throughout: dry-runs for apply-all, short-lived override tokens,
        and the tiny test corpus for reindex.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            return
        session = self.ctx.session
        base = self.ctx.base_url

        cases = [
            # (label, action, method, path, json_body, params)
            (
                "migration_apply_all",
                "migrations.apply_all",
                "POST",
                "/api/v1/admin/database/migrations/apply-all",
                None,
                {"dry_run": "true"},
            ),
            (
                "migration_emergency_override",
                "migrations.emergency_override",
                "POST",
                "/api/v1/admin/migrations/emergency-override",
                {"reason": "self-test ceremony", "expires_minutes": 5},
                None,
            ),
            (
                "search_reindex",
                "search.reindex",
                "POST",
                "/api/v1/admin/search/reindex",
                {},
                None,
            ),
            (
                "search_reindex_standalone",
                "search.reindex",
                "POST",
                "/api/v1/admin/search/reindex/standalone",
                {},
                None,
            ),
        ]
        for label, action, method, path, body, params in cases:
            logger.info(f"Approval-gated test {label} ({method} {path})...")
            if not self._approval_ceremony(action):
                self.ctx.results.append(
                    {
                        "method": method,
                        "path": path,
                        "status_code": 0,
                        "duration_ms": 0,
                        "success": False,
                        "label": label,
                        "error": "approval ceremony failed",
                    }
                )
                continue
            start = time.time()
            try:
                resp = session.request(
                    method, f"{base}{path}", json=body, params=params, timeout=60
                )
                duration = (time.time() - start) * 1000
                ok = 200 <= resp.status_code < 300
                self.ctx.results.append(
                    {
                        "method": method,
                        "path": path,
                        "status_code": resp.status_code,
                        "duration_ms": duration,
                        "success": ok,
                        "label": label,
                    }
                )
                if ok:
                    logger.info(f"Approval-gated {label} PASSED -> {resp.status_code}")
                else:
                    logger.warning(
                        f"Approval-gated {label} -> {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
            except Exception as e:
                logger.warning(f"Approval-gated {label} exception: {e}")
                self.ctx.results.append(
                    {
                        "method": method,
                        "path": path,
                        "status_code": 0,
                        "duration_ms": 0,
                        "success": False,
                        "label": label,
                        "error": str(e)[:200],
                    }
                )
