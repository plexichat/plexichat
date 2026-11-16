"""Regression test for batched metadata/gate lookups in the migration list."""

import asyncio
from unittest.mock import MagicMock, patch

from src.api.routes.admin import migrations as migrations_route


def _response(manager):
    request = MagicMock()
    with (
        patch.object(migrations_route, "check_host_restriction"),
        patch.object(migrations_route, "get_admin_from_token", return_value=1),
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
