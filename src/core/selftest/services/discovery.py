"""
Route discovery service for SelfTestRunner.

Extracts routes from the FastAPI OpenAPI spec and manages exclusions.
"""

from typing import List, Dict, Any, Set

import utils.logger as logger
from utils import licensing as license_module  # type: ignore

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

            self.ctx.openapi_spec = resp.json()
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
        if license_module.has_feature("plexijoin", default=False):
            return set()
        return {403}
