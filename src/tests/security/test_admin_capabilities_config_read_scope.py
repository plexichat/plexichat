"""Regression: the cached admin capability snapshot is config.read-gated.

``GET /admin/capabilities`` is documented (docs/api/admin.md) as requiring the
``config.read`` scope: its payload is derived from system configuration and the
license document, and includes per-feature ``details`` plus a top-level summary
of counts by state. The handler used to call only ``get_admin_from_token``, so
any admin holding a valid session -- including a tickets-only support role with
no configuration scope -- could read the cached snapshot. The short-TTL payload
cache made this a config/license disclosure that bypassed the scope model
entirely.

These tests exercise the real ``require_admin_permission`` path (patched at the
session/DB boundaries only) and prove the gate still runs on a warm cache hit.
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException, Request

import src.api as api
import src.api.routes.admin.capabilities as caps_routes
import src.api.routes.admin.utils as admin_utils
from src.core.admin import permissions as admin_permissions

pytestmark = pytest.mark.security


@pytest.fixture(autouse=True)
def _cold_cache():
    caps_routes.clear_admin_capabilities_payload_cache()
    yield
    caps_routes.clear_admin_capabilities_payload_cache()


def _request():
    return Request({"type": "http", "headers": [], "client": ("127.0.0.1", 0)})


def _wire_gates(monkeypatch, granted):
    """Stub only the credential/DB edges; keep the real scope-check logic."""
    monkeypatch.setattr(caps_routes, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(admin_utils, "get_admin_from_token", lambda r: 1)
    monkeypatch.setattr(api, "get_db", lambda: object())
    monkeypatch.setattr(
        admin_permissions,
        "check_admin_permission",
        lambda admin_id, scope, db: granted["value"] and scope == "config.read",
    )


def test_cached_capability_snapshot_is_denied_without_config_read(monkeypatch):
    granted = {"value": True}
    _wire_gates(monkeypatch, granted)

    evaluations = []
    monkeypatch.setattr(
        caps_routes,
        "get_artifact_capabilities",
        lambda: evaluations.append(1) or {},
    )

    # Prime the snapshot while the scope is granted.
    first = asyncio.run(caps_routes.get_admin_capabilities(_request()))
    assert first["summary"]["total"] == 0
    assert len(evaluations) == 1

    # Revoke only the scope; the cached payload is still warm. The request must
    # fail closed instead of handing the snapshot to an unscoped admin.
    granted["value"] = False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(caps_routes.get_admin_capabilities(_request()))
    assert exc.value.status_code == 403
    assert len(evaluations) == 1  # no producer ran on the denied poll


def test_denied_admin_never_triggers_a_cold_producer(monkeypatch):
    granted = {"value": False}
    _wire_gates(monkeypatch, granted)

    def _boom():
        raise AssertionError("unauthorized caller must not build the payload")

    monkeypatch.setattr(caps_routes, "get_artifact_capabilities", _boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(caps_routes.get_admin_capabilities(_request()))
    assert exc.value.status_code == 403


def test_handler_declares_the_documented_scope():
    source = inspect.getsource(caps_routes.get_admin_capabilities)
    assert 'require_admin_permission(request, "config.read")' in source
    assert "get_admin_from_token(" not in source
