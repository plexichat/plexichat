"""Targeted regressions for two PlexiJoin boundary gaps.

* ``GET /Join/list`` returned federation metadata (remote URLs, notes, peer
  identities) to any authenticated admin with an ``admin_users`` row, without
  the ``admin.plexijoin.manage`` scope the equivalent admin read endpoints
  require. That is an IDOR-style read of federation metadata on the
  client-facing surface.

* ``POST /api/v1/federation/join-request`` passed the signed envelope's
  ``requested_by`` / ``note`` straight into ``submit_inbound_request``. A
  validly signed envelope may legally carry a non-string or over-length value
  (``_bound_payload_value`` only bounds JSON scalars), and ``bound_text`` then
  raises a plain ``HardeningViolation`` — an unhandled 500 on an endpoint that
  has no JWT. Those fields are now bounded at the boundary with a 400 result.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from src.api.routes import federation as mod


SCOPE = "admin.plexijoin.manage"


# --- 1. /Join/list requires the federation management scope ----------------


class _AdminDB:
    def fetch_one(self, query, params=None):
        return {"id": 7}


def _patch_join_auth(monkeypatch, *, permitted: bool):
    from src.api.routes import join as join_routes

    calls: list[tuple[int, str]] = []

    monkeypatch.setattr(
        "src.api.routes.admin.utils.check_host_restriction",
        lambda request, require_admin_ui=False: None,
    )
    monkeypatch.setattr("src.api.get_db", lambda: _AdminDB())

    def _permission(admin_id, scope, db):
        calls.append((admin_id, scope))
        return permitted

    monkeypatch.setattr(
        "src.core.admin.permissions.check_admin_permission", _permission
    )
    return join_routes, calls


@pytest.mark.asyncio
async def test_join_list_denies_admin_without_federation_scope(monkeypatch):
    """An authenticated admin lacking the scope cannot enumerate federation links."""
    join_routes, calls = _patch_join_auth(monkeypatch, permitted=False)
    request = SimpleNamespace(headers={}, client=None)
    user = SimpleNamespace(user_id=7)

    with pytest.raises(join_routes.JoinApiError) as exc:
        await join_routes.list_joins(request, None, user)

    assert exc.value.status_code == 403
    assert calls == [(7, SCOPE)]


@pytest.mark.asyncio
async def test_join_list_still_works_with_federation_scope(monkeypatch):
    """A properly scoped admin can still list joins (no over-correction)."""
    join_routes, calls = _patch_join_auth(monkeypatch, permitted=True)
    manager = MagicMock()
    manager.list_connections.return_value = {"connections": [], "total": 0}
    monkeypatch.setattr(join_routes, "_get_manager", lambda request: manager)

    request = SimpleNamespace(headers={}, client=None)
    user = SimpleNamespace(user_id=7)
    result = await join_routes.list_joins(request, None, user)

    assert result["total"] == 0
    assert calls == [(7, SCOPE)]


# --- 2. join-request bounds optional signed text fields -------------------


def _request(body: bytes) -> Request:
    return cast(
        Request,
        SimpleNamespace(
            headers={"content-type": "application/json"},
            body=AsyncMock(return_value=body),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [
        ("requested_by", 7),
        ("requested_by", {"x": 1}),
        ("note", 7),
        ("note", "x" * 501),  # above the stored field's 500-char bound
    ],
)
async def test_join_request_bad_optional_field_is_400_not_500(field, value):
    """A wrong-typed/over-length signed field is a clean 400, never a 500."""
    body = json.dumps({"instance_id": "peer-1", field: value}).encode()
    request = _request(body)
    manager = MagicMock()
    manager.submit_inbound_request.return_value = {"id": 1}
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=manager),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
    ):
        with pytest.raises(HTTPException) as exc:
            await mod.join_request_inbound(request)

    assert exc.value.status_code == 400
    manager.submit_inbound_request.assert_not_called()


@pytest.mark.asyncio
async def test_join_request_valid_optional_fields_still_recorded():
    """Well-formed signed text still reaches the manager unchanged."""
    body = json.dumps(
        {
            "instance_id": "peer-1",
            "requested_by": "admin-7",
            "note": "hello",
        }
    ).encode()
    request = _request(body)
    manager = MagicMock()
    manager.submit_inbound_request.return_value = {"id": 1}
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=manager),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
    ):
        result = await mod.join_request_inbound(request)

    assert result == {"ok": True, "request_id": 1}
    manager.submit_inbound_request.assert_called_once_with(
        remote_instance_id="peer-1",
        remote_url="",
        requested_by="admin-7",
        note="hello",
    )
