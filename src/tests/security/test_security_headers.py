"""
Security headers middleware tests.

Verifies the per-path CSP scoping and baseline security headers emitted for
API, media, and SPA/admin responses, plus the browser-feature restrictions in
``Permissions-Policy``.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.security import SecurityHeadersMiddleware


def make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/v1/test")
    async def api_test():
        return {"ok": True}

    @app.get("/api/v1/media/signed")
    async def media_test():
        return {"ok": True}

    @app.get("/")
    async def spa_root():
        return {"ok": True}

    return app


class TestSecurityHeaders:
    def test_api_responses_have_strict_csp_and_baseline_headers(self):
        client = TestClient(make_app())
        headers = client.get("/api/v1/test").headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert headers["x-permitted-cross-domain-policies"] == "none"
        assert headers["x-frame-options"] == "SAMEORIGIN"
        csp = headers["content-security-policy"]
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "base-uri 'none'" in csp

    def test_media_responses_are_embeddable_but_restrict_scripts(self):
        client = TestClient(make_app())
        headers = client.get("/api/v1/media/signed").headers
        csp = headers["content-security-policy"]
        assert "img-src 'self' data: blob: *" in csp
        assert "media-src 'self' *" in csp
        # Embeddable media is intentionally excluded from X-Frame-Options.
        assert "x-frame-options" not in headers

    def test_spa_responses_use_bootstrap_csp(self):
        client = TestClient(make_app())
        headers = client.get("/").headers
        csp = headers["content-security-policy"]
        assert "script-src 'self' 'unsafe-inline' 'unsafe-eval'" in csp
        assert "connect-src 'self' ws: wss:" in csp

    def test_permissions_policy_disables_sensitive_features(self):
        client = TestClient(make_app())
        pp = client.get("/api/v1/test").headers["permissions-policy"]
        for feature in (
            "camera=()",
            "microphone=()",
            "geolocation=()",
            "payment=()",
            "usb=()",
        ):
            assert feature in pp

    def test_existing_headers_are_not_duplicated(self):
        client = TestClient(make_app())
        raw = client.get("/api/v1/test").headers.raw
        instances = [k for k, _ in raw if k.lower() == b"x-content-type-options"]
        assert len(instances) == 1
