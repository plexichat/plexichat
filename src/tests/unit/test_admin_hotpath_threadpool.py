"""The blocking admin maintenance work must not run on the event loop.

``database.py`` migration handlers and ``reindex_conversation`` perform
synchronous discovery/decryption work. They now dispatch that work through
``run_in_threadpool`` so concurrent admin requests are not serialized behind a
single blocking call. These tests pin the dispatch while keeping the existing
permission gates and error mapping intact.
"""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from src.api.schemas.common import SnowflakeIDParam
from src.api.routes.admin import database as database_routes
from src.api.routes.admin import reindex as reindex_routes


def _patch_gates(monkeypatch, module):
    monkeypatch.setattr(module, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(module, "require_admin_permission", lambda request, perm: 1)
    monkeypatch.setattr(module, "require_approved_request", lambda action: None)
    monkeypatch.setattr(
        module, "require_destructive_confirmation", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(module, "validate_migration_version", lambda version: version)


def _capture_threadpool(monkeypatch, module, result):
    calls = []

    async def _fake_run(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return result() if callable(result) else result

    monkeypatch.setattr(module, "run_in_threadpool", _fake_run)
    return calls


@pytest.mark.unit
def test_apply_all_dispatches_migration_work_off_loop(monkeypatch):
    _patch_gates(monkeypatch, database_routes)
    monkeypatch.setattr(database_routes.api, "get_db", lambda: object())
    monkeypatch.setattr(
        database_routes,
        "MigrationManager",
        lambda db: SimpleNamespace(apply_all_pending=lambda dry_run: None),
    )
    calls = _capture_threadpool(monkeypatch, database_routes, {"applied": 1})

    result = asyncio.run(
        database_routes.apply_all_migrations(
            cast(Request, SimpleNamespace()), dry_run=True
        )
    )

    assert result == {"applied": 1}
    assert len(calls) == 1
    assert calls[0][2] == {"dry_run": True}


@pytest.mark.unit
def test_rollback_dispatches_migration_work_off_loop(monkeypatch):
    _patch_gates(monkeypatch, database_routes)
    monkeypatch.setattr(database_routes.api, "get_db", lambda: object())
    monkeypatch.setattr(
        database_routes,
        "MigrationManager",
        lambda db: SimpleNamespace(rollback_migration=lambda version: None),
    )
    calls = _capture_threadpool(monkeypatch, database_routes, {"success": True})

    result = asyncio.run(
        database_routes.rollback_migration(cast(Request, SimpleNamespace()), "001")
    )

    assert result == {"success": True}
    assert len(calls) == 1
    assert calls[0][1] == ("001",)


@pytest.mark.unit
def test_reindex_conversation_dispatches_off_loop(monkeypatch):
    monkeypatch.setattr(reindex_routes, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        reindex_routes, "require_admin_permission", lambda request, perm: 1
    )
    monkeypatch.setattr(reindex_routes, "_require_search_ready", lambda: None)
    manager = SimpleNamespace(reindex_conversation=lambda conversation_id, force: 3)
    monkeypatch.setattr(reindex_routes.search_module, "_manager", manager)
    calls = _capture_threadpool(monkeypatch, reindex_routes, 3)

    result = asyncio.run(
        reindex_routes.reindex_conversation(
            request=cast(Request, SimpleNamespace()),
            conversation_id=SnowflakeIDParam(42),
            body=None,
        )
    )

    assert result["indexed"] == 3
    assert len(calls) == 1
    assert calls[0][1] == (42, False)


@pytest.mark.unit
def test_reindex_conversation_still_maps_failures_to_500(monkeypatch):
    monkeypatch.setattr(reindex_routes, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        reindex_routes, "require_admin_permission", lambda request, perm: 1
    )
    monkeypatch.setattr(reindex_routes, "_require_search_ready", lambda: None)
    manager = SimpleNamespace(
        reindex_conversation=lambda conversation_id, force: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
    )
    monkeypatch.setattr(reindex_routes.search_module, "_manager", manager)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            reindex_routes.reindex_conversation(
                request=cast(Request, SimpleNamespace()),
                conversation_id=SnowflakeIDParam(42),
                body=None,
            )
        )
    assert exc.value.status_code == 500
