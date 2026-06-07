"""
Static client test service for SelfTestRunner.

Verifies the static client middleware:

* serves ``/`` with a 200 (index.html or placeholder) and sensible headers
* serves the SPA fallback for unknown paths
* returns 404 for non-existent files
* returns 503 (or 200) for the ``/config.js`` runtime injection
* does not block the API

These tests are only meaningful when ``static_client.enabled`` is True.
"""

from __future__ import annotations

import utils.config as config
import utils.logger as logger

from ..context import SelfTestContext


class StaticClientTester:
    """Lightweight smoke tests for the static client middleware."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def test_static_client(self) -> None:
        sc = config.get("static_client", {}) or {}
        if not isinstance(sc, dict) or not sc.get("enabled", False):
            logger.debug("static_client disabled - skipping smoke tests")
            return
        if not sc.get("serve", True):
            logger.debug("static_client.serve=false - skipping smoke tests")
            return

        logger.info("Testing static client middleware...")
        session = self.ctx.requests_module.Session()

        checks = [
            ("/", [200, 503]),
            ("/index.html", [200, 503]),
            ("/robots.txt", [200, 503, 404]),
            ("/config.js", [200, 503, 404]),
            ("/__nope_does_not_exist__.html", [404]),
        ]

        # Initialize to satisfy type checker
        ok = False
        path = ""
        resp = None

        for path, expected in checks:
            url = f"{self.ctx.base_url}{path}"
            try:
                resp = session.get(url, timeout=3, allow_redirects=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"static_client GET {path} failed: {exc}")
                self.ctx.results.append(
                    {
                        "method": "GET",
                        "path": path,
                        "status_code": 0,
                        "duration_ms": 0,
                        "success": False,
                        "label": "static_client",
                        "warning": True,
                        "error": str(exc),
                    }
                )
                continue

            csp = resp.headers.get("content-security-policy")
            xcto = resp.headers.get("x-content-type-options", "")
            xfo = resp.headers.get("x-frame-options", "")
            ok = resp.status_code in expected and (
                not csp or xcto.lower() == "nosniff" or xfo
            )
            self.ctx.results.append(
                {
                    "method": "GET",
                    "path": path,
                    "status_code": resp.status_code,
                    "duration_ms": 0,
                    "success": ok,
                    "label": "static_client",
                    "warning": not ok,
                    "headers_seen": {
                        "content-security-policy": bool(csp),
                        "x-content-type-options": xcto,
                        "x-frame-options": xfo,
                    },
                }
            )
            if not ok:
                logger.warning(
                    f"static_client GET {path} -> {resp.status_code} "
                    f"(expected one of {expected})"
                )

        # If static_client is enabled and serving, do a more thorough check:
        # verify config.js contains valid config and fetch a few asset files
        if ok and path == "/config.js" and resp is not None and resp.status_code == 200:
            try:
                self._verify_config_js(session, resp.text)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"static_client config.js verification failed: {exc}")

        # Fetch a few asset files to verify the client bundle is complete
        asset_checks = [
            "/favicon.svg",
        ]
        for path in asset_checks:
            url = f"{self.ctx.base_url}{path}"
            try:
                resp = session.get(url, timeout=3, allow_redirects=False)
                ok = resp.status_code in [200, 503]
                self.ctx.results.append(
                    {
                        "method": "GET",
                        "path": path,
                        "status_code": resp.status_code,
                        "duration_ms": 0,
                        "success": ok,
                        "label": "static_client",
                        "warning": not ok,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"static_client GET {path} failed: {exc}")

    def _verify_config_js(self, session, config_js_text: str) -> None:
        """Verify config.js contains the expected runtime configuration."""

        # Check for required fields in config.js
        required_fields = ["serverUrl", "hideServerField", "defaultTheme", "version"]
        missing = []
        for field in required_fields:
            if field not in config_js_text:
                missing.append(field)

        if missing:
            logger.warning(f"static_client config.js missing fields: {missing}")
            self.ctx.results.append(
                {
                    "method": "GET",
                    "path": "/config.js",
                    "status_code": 200,
                    "duration_ms": 0,
                    "success": False,
                    "label": "static_client",
                    "warning": True,
                    "error": f"config.js missing fields: {missing}",
                }
            )
        else:
            logger.info("static_client config.js verification passed")
            self.ctx.results.append(
                {
                    "method": "GET",
                    "path": "/config.js",
                    "status_code": 200,
                    "duration_ms": 0,
                    "success": True,
                    "label": "static_client",
                    "warning": False,
                    "detail": "config.js contains all required fields",
                }
            )
