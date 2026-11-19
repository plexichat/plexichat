"""Regressions for auth-only privileged reads on the admin surface.

Two related authorization gaps are pinned here:

* The legacy moderator queue/list/count endpoints validated only a session
  token (``get_admin_from_token``) and returned reports, blocked users/hashes
  and the global AutoMod configuration without checking any permission scope.
  Their siblings ``/reports`` and ``/reports/counts`` already gate on
  ``reports.read``, and the corresponding write scopes exist for every list,
  so an admin holding any unrelated scope could still read all of this data.
* ``GET /dashboard/cross-worker-tuning/audit`` read the same ``admin_audit_log``
  (admin ids, usernames, IPs, details) as the ``/audit`` endpoints with
  authentication only, bypassing the declared ``admin.audit.read`` scope.

Every gate added is a strict strengthening: ``require_admin_permission`` still
validates the token before checking the scope.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

import src.api as api
from src.api.routes.admin import dashboard, moderation


pytestmark = pytest.mark.unit


# handler name -> scope that must gate it
_MODERATION_READ_SCOPES = {
    "get_hash_reports": "reports.read",
    "get_hash_report_counts": "reports.read",
    "get_message_reports": "reports.read",
    "get_message_report_counts": "reports.read",
    "get_user_reports": "reports.read",
    "get_user_report_counts": "reports.read",
    "get_blocked_hashes": "blocked_hashes.read",
    "get_blocked_users": "blocked_users.read",
    "get_automod_rules": "automod.read",
    "get_automod_config": "automod.read",
}

# Positional arguments a handler needs beyond the request.
_EXTRA_ARGS = {"get_automod_rules": (1,)}  # server_id


def _req() -> Request:
    return cast(Request, SimpleNamespace())


class _ForbiddenDB:
    """Any data access below a denied gate is a test failure."""

    def fetch_all(self, *args, **kwargs):
        raise AssertionError("data was read despite a denied scope")

    def fetch_one(self, *args, **kwargs):
        raise AssertionError("data was read despite a denied scope")

    def execute(self, *args, **kwargs):
        raise AssertionError("data was read despite a denied scope")


@pytest.mark.parametrize("name,scope", sorted(_MODERATION_READ_SCOPES.items()))
def test_moderation_reads_declare_an_explicit_scope(name: str, scope: str) -> None:
    source = inspect.getsource(getattr(moderation, name))
    assert f'require_admin_permission(request, "{scope}")' in source, name
    # Authentication alone is never enough for these reads.
    assert "get_admin_from_token(" not in source, name


def test_tuning_audit_read_declares_the_audit_scope() -> None:
    source = inspect.getsource(dashboard.get_cross_worker_tuning_audit)
    assert 'require_admin_permission(request, "admin.audit.read")' in source
    assert "get_admin_from_token(" not in source


@pytest.mark.parametrize("name", sorted(_MODERATION_READ_SCOPES))
def test_denied_scope_stops_moderation_read_before_any_query(
    monkeypatch, name: str
) -> None:
    def _deny(request, permission):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    monkeypatch.setattr(moderation, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(moderation, "require_admin_permission", _deny)
    monkeypatch.setattr(api, "get_db", _ForbiddenDB)
    monkeypatch.setattr(api, "get_reports", lambda: _ForbiddenDB())

    handler = getattr(moderation, name)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler(_req(), *_EXTRA_ARGS.get(name, ())))
    assert exc.value.status_code == 403


def test_denied_audit_scope_stops_tuning_audit_read(monkeypatch) -> None:
    def _deny(request, permission):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    monkeypatch.setattr(dashboard, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(dashboard, "require_admin_permission", _deny)
    monkeypatch.setattr(api, "get_db", _ForbiddenDB)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(dashboard.get_cross_worker_tuning_audit(_req(), limit=20))
    assert exc.value.status_code == 403


class _GateReached(Exception):
    """Raised from the gate stub to capture the scope it was asked for."""

    def __init__(self, scope: str):
        super().__init__(scope)
        self.scope = scope


@pytest.mark.parametrize("name,scope", sorted(_MODERATION_READ_SCOPES.items()))
def test_gate_is_asked_for_the_expected_scope(
    monkeypatch, name: str, scope: str
) -> None:
    """No accidental copy/paste: the handler asks for the intended scope."""

    def _capture(request, permission):
        raise _GateReached(permission)

    monkeypatch.setattr(moderation, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(moderation, "require_admin_permission", _capture)

    handler = getattr(moderation, name)
    with pytest.raises(_GateReached) as exc:
        asyncio.run(handler(_req(), *_EXTRA_ARGS.get(name, ())))
    assert exc.value.scope == scope


def test_audit_gate_is_asked_for_the_expected_scope(monkeypatch) -> None:
    def _capture(request, permission):
        raise _GateReached(permission)

    monkeypatch.setattr(dashboard, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(dashboard, "require_admin_permission", _capture)

    with pytest.raises(_GateReached) as exc:
        asyncio.run(dashboard.get_cross_worker_tuning_audit(_req(), limit=20))
    assert exc.value.scope == "admin.audit.read"
