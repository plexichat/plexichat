"""Regression: migration metadata must be discovered from named files.

Migration sources are named ``{version}_{name}.py`` (for example
``006_report_queue_indexes.py``). ``get_migration_metadata()`` used to look for
a bare ``{version}.py``, so every real lookup missed and the admin status/list
and detail endpoints silently reported empty metadata.

These tests pin that discovery by version prefix works and that the parsed
metadata is surfaced by the endpoints, while keeping the AST-only parse (no
``exec_module``).
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.api.routes.admin import migrations as migrations_route
from src.core.migrations import tracker as tracker_module
from src.core.migrations.tracker import MigrationTracker


EXPECTED_METADATA = {
    "description": "report queue indexes",
    "irreversible": True,
    "depends_on": ["005"],
}

MIGRATION_SOURCE = (
    '"""Report queue indexes.\n\n'
    "MIGRATION_METADATA:\n"
    '{"description": "report queue indexes", "irreversible": true, '
    '"depends_on": ["005"]}\n'
    '"""\n'
    "def up(db): pass\n"
    "def down(db): pass\n"
)


@pytest.fixture
def named_migration_tracker(monkeypatch, tmp_path, db):
    """Tracker backed by a temp dir containing ``006_report_queue_indexes.py``."""
    fake_module = tmp_path / "tracker.py"
    fake_module.touch()
    monkeypatch.setattr(tracker_module, "__file__", str(fake_module))

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "006_report_queue_indexes.py").write_text(MIGRATION_SOURCE)
    tracker_module._clear_metadata_cache()
    return MigrationTracker(db), migrations_dir


@pytest.mark.migrations
def test_metadata_found_for_version_named_migration(named_migration_tracker):
    tracker, _ = named_migration_tracker

    assert tracker.get_migration_metadata("006") == EXPECTED_METADATA


@pytest.mark.migrations
def test_metadata_lookup_still_rejects_unrelated_version(named_migration_tracker):
    tracker, _ = named_migration_tracker

    # "60" must not pick up "006_..." and a missing version stays empty.
    assert tracker.get_migration_metadata("60") == {}
    assert tracker.get_migration_metadata("999") == {}


@pytest.mark.migrations
def test_list_endpoint_exposes_discovered_metadata(named_migration_tracker):
    tracker, _ = named_migration_tracker
    manager = MagicMock()
    manager.get_migration_status.return_value = {
        "applied_migrations": [],
        "pending_migrations": ["006"],
        "failed_migrations": [],
        "all_records": [],
    }
    manager.tracker = tracker

    with (
        patch.object(
            tracker, "can_run_irreversible_migration", return_value=(True, "ok")
        ),
        patch.object(migrations_route, "check_host_restriction"),
        patch.object(migrations_route, "get_admin_from_token", return_value=1),
        # The handler re-checks the read scope in its body (the dependency
        # authenticates, the body authorizes); this unit test already supplies
        # the authenticated admin id, so stub the scope gate it does not test.
        patch.object(migrations_route, "require_admin_permission", return_value=1),
    ):
        response = asyncio.run(
            migrations_route.list_migrations(MagicMock(), manager=manager, admin_user=1)
        )

    info = next(item for item in response.migrations if item.version == "006")
    assert info.metadata == EXPECTED_METADATA
    assert info.is_irreversible is True
    assert info.depends_on == ["005"]


@pytest.mark.migrations
def test_detail_endpoint_exposes_discovered_metadata(named_migration_tracker):
    tracker, migrations_dir = named_migration_tracker
    manager = MagicMock()
    manager.tracker.get_migration_status.return_value = None
    manager.get_migration_status.return_value = {"pending_migrations": ["006"]}
    manager.tracker.get_migration_metadata.side_effect = tracker.get_migration_metadata
    manager.tracker.can_run_irreversible_migration.return_value = (True, "ok")

    with (
        patch.object(
            migrations_route.validator,
            "get_migration_files",
            return_value=[("006", str(migrations_dir / "006_report_queue_indexes.py"))],
        ),
        patch.object(migrations_route, "check_host_restriction"),
        patch.object(migrations_route, "get_admin_from_token", return_value=1),
        patch.object(migrations_route, "require_admin_permission", return_value=1),
    ):
        response = asyncio.run(
            migrations_route.get_migration_details(
                MagicMock(), "006", manager=manager, admin_user=1
            )
        )

    assert response.name == "report_queue_indexes"
    assert response.metadata == EXPECTED_METADATA
    assert response.is_irreversible is True
    assert response.depends_on == ["005"]
