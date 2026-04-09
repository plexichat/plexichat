"""Targeted tests for admin UI response hardening."""

import asyncio

import pytest
from starlette.requests import Request

import src.api.routes.admin.ui as admin_ui


def _build_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


class TestAdminUISecurity:
    """Tests for cache-busting and CSP headers on admin HTML pages."""

    @pytest.mark.parametrize(
        ("handler", "path", "template_name"),
        [
            (admin_ui.admin_login_page, "/admin/login", "login.html"),
            (admin_ui.admin_dashboard_page, "/admin/ui-dashboard", "dashboard.html"),
        ],
    )
    def test_admin_pages_set_no_store_and_csp_headers(
        self, handler, path, template_name, monkeypatch
    ):
        """Admin pages should be uncached and served with a nonce-based CSP."""
        monkeypatch.setattr(admin_ui, "check_host_restriction", lambda request: None)
        monkeypatch.setattr(admin_ui, "generate_csp_nonce", lambda: "test-nonce")
        monkeypatch.setattr(
            admin_ui,
            "build_admin_csp_header",
            lambda nonce: f"default-src 'self'; script-src 'nonce-{nonce}'",
        )
        monkeypatch.setattr(
            admin_ui,
            "load_admin_template",
            lambda name, csp_nonce: f"{name}:{csp_nonce}",
        )

        response = asyncio.run(handler(_build_request(path)))

        assert response.headers["Cache-Control"] == (
            "no-store, no-cache, must-revalidate, max-age=0, private"
        )
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["Expires"] == "0"
        assert (
            response.headers["Content-Security-Policy"]
            == "default-src 'self'; script-src 'nonce-test-nonce'"
        )
        assert response.body.decode() == f"{template_name}:test-nonce"
