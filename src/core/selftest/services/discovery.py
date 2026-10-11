"""
Route discovery service for SelfTestRunner.

Extracts routes from the FastAPI OpenAPI spec and manages exclusions.
"""

from typing import List, Dict, Any, Set

import utils.logger as logger
from utils import licensing as license_module

from ..context import SelfTestContext


class RouteDiscovery:
    """Discovers routes via OpenAPI and provides feature-gating helpers."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def discover_routes(self) -> List[Dict[str, Any]]:
        try:
            resp = self.ctx.session.get(f"{self.ctx.base_url}/openapi.json", timeout=10)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch OpenAPI spec: {resp.status_code}")
                return []
            spec = resp.json()

            self.ctx.openapi_spec = spec
            routes = []

            for path, methods in self.ctx.openapi_spec.get("paths", {}).items():
                for method, details in methods.items():
                    if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                        if any(
                            x in path
                            for x in ("/docs", "/redoc", "/openapi.json", "/health")
                        ):
                            continue

                        routes.append(
                            {
                                "path": path,
                                "method": method.upper(),
                                "summary": details.get("summary", ""),
                                "operation_id": details.get("operationId", ""),
                                "parameters": details.get("parameters", []),
                                "request_body": details.get("requestBody", {}),
                            }
                        )
            return sorted(routes, key=lambda x: x["path"].replace("{", "!"))
        except Exception as e:
            logger.error(f"Route discovery failed: {e}")
            return []

    def plexijoin_expected_statuses(self) -> Set[int]:
        """Status codes that are *expected*, not failures, for PlexiJoin routes.

        On a licensed install the feature is available and these routes must
        answer 2xx, so the set is empty. On a free-tier install the feature
        is unavailable and every license-gated route correctly answers 403,
        so 403 is expected rather than a regression.

        ``licensing.setup()`` may not have run (for example, a unit test that
        constructs ``SelfTestRunner`` directly). Treat that as unlicensed
        instead of raising, so discovery never crashes the suite.
        """
        try:
            if license_module.has_feature("plexijoin", default=False):
                return set()
        except RuntimeError:
            return {403}
        return {403}

    def is_plexijoin_gated(self, path: str) -> bool:
        """True for routes gated behind the PlexiJoin (federation) license.

        Mirrors the route classes the ``--unlicensed`` exclusion in
        ``runner.py`` recognises: the admin PlexiJoin management routes and
        the inbound artifact federation routes, which share the PlexiJoin
        feature check.
        """
        return "/plexijoin/" in path or path.startswith("/api/v1/federation/artifact-")

    #: Message both licence-gated route families return on a free-tier
    #: install (admin/plexijoin.py and federation.py).
    _PLEXIJOIN_DENIAL_MARKER = "not licensed"

    def expected_plexijoin_denial(
        self, path: str, status_code: int, body_text: str
    ) -> bool:
        """True only for the *licence-gate* denial on a PlexiJoin route.

        A 403 on a gated path is expected only when the server is unlicensed
        AND the response body carries the licence-denial message. Checking the
        body (as ``suite._expected_capability_denial`` does for the capability
        gate) keeps a genuine authorization failure -- an admin lacking
        ``admin.plexijoin.manage`` -- from being masked as an expected licence
        denial on a free-tier install.
        """
        if not self.is_plexijoin_gated(path):
            return False
        if status_code not in self.plexijoin_expected_statuses():
            return False
        return self._PLEXIJOIN_DENIAL_MARKER in (body_text or "").lower()
