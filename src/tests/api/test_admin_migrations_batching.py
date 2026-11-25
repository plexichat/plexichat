"""Regression test for batched metadata/gate lookups in the migration list."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.admin import migrations as migrations_route


def _response(manager):
    request = MagicMock()
    with (
        patch.object(migrations_route, "check_host_restriction"),
        patch.object(migrations_route, "get_admin_from_token", return_value=1),
        # The handler re-checks the read scope in its body; this unit test
        # supplies the authenticated admin id and stubs the scope gate.
        patch.object(migrations_route, "require_admin_permission", return_value=1),
    ):
        return asyncio.run(
            migrations_route.list_migrations(request, manager=manager, admin_user=1)
        )


def test_list_migrations_parses_metadata_once_per_version():
    manager = MagicMock()
    manager.get_migration_status.return_value = {
        "applied_migrations": ["001"],
        "pending_migrations": ["002", "003"],
        "failed_migrations": [],
        "all_records": [
            {"version": "001", "name": "one", "status": "completed"},
            {"version": "002", "name": "two", "status": "pending"},
        ],
    }
    metadata = {
        "001": {},
        "002": {"irreversible": True, "depends_on": ["001"]},
        "003": {},
    }
    manager.tracker.get_migration_metadata.side_effect = lambda v: metadata[v]
    manager.tracker.can_run_irreversible_migration.return_value = (True, "ok")

    result = _response(manager)

    assert [m.version for m in result.migrations] == ["001", "002", "003"]
    assert manager.tracker.get_migration_metadata.call_count == 3

    gate_calls = manager.tracker.can_run_irreversible_migration.call_args_list
    assert len(gate_calls) == 2
    for call in gate_calls:
        assert "metadata" in call.kwargs, (
            "gate re-looked-up metadata instead of reusing it"
        )


def test_pending_detail_reuses_cached_status_snapshot():
    manager = MagicMock()
    manager.tracker.get_migration_status.return_value = None
    manager.get_migration_status.return_value = {"pending_migrations": ["002"]}
    manager.tracker.get_migration_metadata.return_value = {}
    manager.tracker.can_run_irreversible_migration.return_value = (True, "ok")

    with (
        patch.object(
            migrations_route.validator,
            "get_migration_files",
            return_value=[("002", "/tmp/002_add_widgets.py")],
        ),
        patch.object(migrations_route, "check_host_restriction"),
        patch.object(migrations_route, "get_admin_from_token", return_value=1),
        # The handler re-checks the read scope in its body; this unit test
        # supplies the authenticated admin id and stubs the scope gate.
        patch.object(migrations_route, "require_admin_permission", return_value=1),
    ):
        result = asyncio.run(
            migrations_route.get_migration_details(
                MagicMock(), "002", manager=manager, admin_user=1
            )
        )

    assert result.name == "add_widgets"
    manager.get_pending_migrations.assert_not_called()


def test_pending_detail_still_404s_for_unknown_version():
    manager = MagicMock()
    manager.tracker.get_migration_status.return_value = None
    manager.get_migration_status.return_value = {"pending_migrations": []}

    with (
        patch.object(migrations_route, "check_host_restriction"),
        patch.object(migrations_route, "get_admin_from_token", return_value=1),
        # The handler re-checks the read scope in its body; this unit test
        # supplies the authenticated admin id and stubs the scope gate.
        patch.object(migrations_route, "require_admin_permission", return_value=1),
        pytest.raises(HTTPException) as exc,
    ):
        asyncio.run(
            migrations_route.get_migration_details(
                MagicMock(), "999", manager=manager, admin_user=1
            )
        )

    assert exc.value.status_code == 404
