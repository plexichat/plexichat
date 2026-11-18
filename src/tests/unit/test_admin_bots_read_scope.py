"""Regression tests: admin bot-oversight READs require a permission scope.

The three ``GET /admin/bots/*`` handlers used to call a module-local
``_require_admin`` helper that only validated the admin *token* (via
``get_admin_from_token``) and never checked a permission scope. Every other
admin read route enforces a concrete scope, so any authenticated low-privilege
admin could enumerate application owners, bot requesters, server ids, request
reasons and system-wide statistics. These reads now carry ``admin.bots.read``.
"""

import asyncio
import inspect
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from src.api.routes.admin import bots as bots_routes
from src.core.admin.permissions import DELEGABLE_PERMISSIONS, PERMISSION_SCOPES

SCOPE = "admin.bots.read"


def _request() -> Request:
    # The handlers only pass this through to the (patched) auth helpers.
    return cast(Request, SimpleNamespace())


HANDLERS = (
    (bots_routes.admin_bot_stats, (), "get_admin_bot_stats"),
    (bots_routes.admin_list_applications, (), "get_admin_bot_applications"),
    (bots_routes.admin_list_requests, (), "get_admin_bot_requests"),
)


def test_scope_is_a_declared_delegable_permission():
    assert SCOPE in PERMISSION_SCOPES
    assert SCOPE in DELEGABLE_PERMISSIONS


@pytest.mark.parametrize("handler,args,data_fn", HANDLERS)
def test_read_handler_source_requires_scoped_permission(handler, args, data_fn):
    """Pin the gate so a future refactor cannot silently drop the scope."""
    source = inspect.getsource(handler)
    assert f'require_admin_permission(request, "{SCOPE}")' in source
    assert "get_admin_from_token" not in source
    assert "_require_admin" not in source


@pytest.mark.parametrize("handler,args,data_fn", HANDLERS)
def test_handler_denies_admin_without_scope(monkeypatch, handler, args, data_fn):
    """A valid admin token with the wrong scope must be refused before any read."""
    calls = []

    def _deny(request, permission):
        calls.append(permission)
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    def _unreachable(*a, **kw):
        raise AssertionError("data access must not be reached without the scope")

    monkeypatch.setattr(bots_routes, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(bots_routes, "require_admin_permission", _deny)
    monkeypatch.setattr(bots_routes.applications, data_fn, _unreachable)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler(_request(), *args))
    assert exc.value.status_code == 403
    assert calls == [SCOPE]


@pytest.mark.parametrize("handler,args,data_fn", HANDLERS)
def test_handler_allows_admin_with_scope(monkeypatch, handler, args, data_fn):
    """A correctly scoped admin still reaches the data layer."""
    calls = []

    def _allow(request, permission):
        calls.append(permission)
        return 7

    monkeypatch.setattr(bots_routes, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(bots_routes, "require_admin_permission", _allow)
    if data_fn == "get_admin_bot_stats":
        monkeypatch.setattr(bots_routes.applications, data_fn, lambda: {})
    else:
        monkeypatch.setattr(bots_routes.applications, data_fn, lambda **kw: [])

    result = asyncio.run(handler(_request(), *args))
    assert calls == [SCOPE]
    assert result is not None
