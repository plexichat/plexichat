"""The provisional-license admin host gate must fail closed on import failure.

``check_host_restriction`` enforces that a provisional/test license (which is
unsigned and enables every feature) may only administer the instance from its
allowed hosts. It used to swallow an ``ImportError`` from the licensing module
and carry on, so a broken/removed licensing module silently disabled that
guard and let a test-licensed instance be administered from any network peer.
"""

import builtins

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import src.api.routes.admin.utils as admin_utils

pytestmark = pytest.mark.security


_NETWORK_PEER = "203.0.113.10"


def _request(client_host=_NETWORK_PEER):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/dashboard",
            "headers": [],
            "client": (client_host, 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    """Make every other host gate permissive so only the license gate matters.

    ``host_restriction.enabled = False`` means the ordinary configured allowlist
    is deliberately off. If the provisional-license gate fails open, the
    request passes with no exception and the regression test fails.
    """
    cfg = {
        "admin_ui": {
            "enabled": True,
            "host_restriction": {"enabled": False},
        },
        "api": {"trusted_proxies": []},
    }
    monkeypatch.setattr(
        admin_utils.config, "get", lambda key, default=None: cfg.get(key, default)
    )
    monkeypatch.setattr(
        admin_utils, "is_authenticated_self_test", lambda request: False
    )


def test_denies_admin_when_licensing_module_cannot_be_imported(monkeypatch):
    real_import = builtins.__import__

    def fail_licensing_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "src.utils" and "licensing" in (fromlist or ()):
            raise ImportError("licensing unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_licensing_import)

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.check_host_restriction(_request())

    assert exc_info.value.status_code == 403


def test_still_allows_when_licensing_is_available_and_not_provisional(monkeypatch):
    """The fix must not turn the gate into a blanket admin deny."""
    from src.utils import licensing

    monkeypatch.setattr(licensing, "is_provisional", lambda: False)

    # No exception: every other host gate was disabled by the fixture.
    admin_utils.check_host_restriction(_request())


def test_still_denies_provisional_license_from_disallowed_host(monkeypatch):
    """The original provisional-license host check is unchanged."""
    from src.utils import licensing

    monkeypatch.setattr(licensing, "is_provisional", lambda: True)
    monkeypatch.setattr(licensing, "is_license_valid_for_host", lambda ip: False)

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.check_host_restriction(_request())

    assert exc_info.value.status_code == 403
