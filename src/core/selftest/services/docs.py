"""
Docs test service for SelfTestRunner.

Verifies the documentation server endpoints are accessible.
"""

from __future__ import annotations

import utils.config as config
import utils.logger as logger

from ..context import SelfTestContext


class DocsTester:
    """Smoke tests for the documentation server."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def _add_bypass_header(self, session) -> None:
        """Add rate-limit bypass header to session if available."""
        rl_config = config.get("rate_limiting", {})
        bypass_secret = rl_config.get("bypass_secret")
        if bypass_secret:
            session.headers.update({"X-RateLimit-Bypass": bypass_secret})
            logger.debug(
                f"Added X-RateLimit-Bypass header to docs session: {bypass_secret[:8]}..."
            )
        else:
            logger.warning(
                "No rate_limiting.bypass_secret found in config - docs may be rate limited"
            )

    def test_docs(self) -> None:
        docs_cfg = config.get("docs", {}) or {}
        if not isinstance(docs_cfg, dict):
            logger.debug("docs config missing - skipping docs tests")
            return

        docs_path = docs_cfg.get("path", "/docs/api")
        if not docs_path:
            logger.debug("docs.path not configured - skipping docs tests")
            return

        logger.info("Testing documentation server...")
        session = self.ctx.requests_module.Session()
        self._add_bypass_header(session)

        # Core docs endpoints to test
        docs_checks = [
            f"{docs_path}/",
            f"{docs_path}/getting-started",
            f"{docs_path}/deployment",
            f"{docs_path}/configuration",
            f"{docs_path}/features",
            f"{docs_path}/permissions",
            f"{docs_path}/security",
            f"{docs_path}/keyrings",
            f"{docs_path}/performance",
            f"{docs_path}/reference",
            f"{docs_path}/websocket",
            f"{docs_path}/rate-limits",
            f"{docs_path}/errors",
            f"{docs_path}/data-types",
            f"{docs_path}/default-config",
            f"{docs_path}/config-authentication",
            f"{docs_path}/config-database",
            f"{docs_path}/config-redis",
            f"{docs_path}/config-media",
            f"{docs_path}/config-voice",
            f"{docs_path}/config-websocket",
            f"{docs_path}/config-search",
            f"{docs_path}/config-rate-limiting",
            f"{docs_path}/config-api",
            f"{docs_path}/config-email",
            f"{docs_path}/config-embeds",
            f"{docs_path}/end-user/getting-started",
            f"{docs_path}/deployment/overview",
            f"{docs_path}/deployment/requirements",
            f"{docs_path}/deployment/getting-started",
            f"{docs_path}/deployment/index",
            f"{docs_path}/migrations",
            f"{docs_path}/migration-reference",
            f"{docs_path}/admin",
            f"{docs_path}/admin/getting-started",
        ]

        for path in docs_checks:
            url = f"{self.ctx.base_url}{path}"
            try:
                resp = session.get(url, timeout=5, allow_redirects=False)
                ok = resp.status_code == 200
                self.ctx.results.append(
                    {
                        "method": "GET",
                        "path": path,
                        "status_code": resp.status_code,
                        "duration_ms": 0,
                        "success": ok,
                        "label": "docs",
                        "warning": not ok,
                    }
                )
                if not ok:
                    logger.warning(
                        f"docs GET {path} -> {resp.status_code} (expected 200)"
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"docs GET {path} failed: {exc}")
                self.ctx.results.append(
                    {
                        "method": "GET",
                        "path": path,
                        "status_code": 0,
                        "duration_ms": 0,
                        "success": False,
                        "label": "docs",
                        "warning": True,
                        "error": str(exc),
                    }
                )
