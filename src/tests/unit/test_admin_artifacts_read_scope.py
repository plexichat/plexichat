"""Regression tests: admin artifact READ routes require a permission scope.

``GET /admin/artifacts`` (lists artifact metadata across every server) and
``GET /admin/artifacts/features/{feature}`` (reads per-server feature overrides)
previously accepted any valid admin token. The feature-settings read even
discarded the authenticated identity entirely. Both are privileged reads and
must carry the same ``admin.artifacts.manage`` scope as the neighbouring
force-delete / retention writes.
"""

import asyncio
import inspect
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from src.api.routes.admin import artifacts as artifacts_routes
from src.api.schemas.common import SnowflakeIDParam

SCOPE = "admin.artifacts.manage"


def _request() -> Request:
    # The handlers only pass this through to the (patched) auth helpers.
    return cast(Request, SimpleNamespace())


def _deny_without_scope(monkeypatch, calls):
    def _deny(request, permission):
        calls.append(permission)
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    monkeypatch.setattr(artifacts_routes, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(artifacts_routes, "require_admin_permission", _deny)


@pytest.mark.parametrize(
    "handler",
    [
        artifacts_routes.admin_list_artifacts,
        artifacts_routes.admin_get_feature_settings,
    ],
)
def test_read_handler_source_requires_scoped_permission(handler):
    """Pin the gate so a future refactor cannot silently drop the scope."""
    source = inspect.getsource(handler)
    assert f'require_admin_permission(request, "{SCOPE}")' in source
    assert "get_admin_from_token(request)" not in source


def test_list_artifacts_rejects_token_without_scope(monkeypatch):
    calls = []
    _deny_without_scope(monkeypatch, calls)

    def _no_manager():
        raise AssertionError("manager must not be reached without the scope")

    monkeypatch.setattr(artifacts_routes, "_get_manager", _no_manager)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(artifacts_routes.admin_list_artifacts(_request()))
    assert exc.value.status_code == 403
    assert calls == [SCOPE]


def test_get_feature_settings_rejects_token_without_scope(monkeypatch):
    calls = []
    _deny_without_scope(monkeypatch, calls)

    def _no_manager():
        raise AssertionError("manager must not be reached without the scope")

    monkeypatch.setattr(artifacts_routes, "_get_manager", _no_manager)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            artifacts_routes.admin_get_feature_settings(
                feature="voice",
                server_id=SnowflakeIDParam(42),
                request=_request(),
            )
        )
    assert exc.value.status_code == 403
    assert calls == [SCOPE]
