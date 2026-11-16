"""Performance regression tests for migration metadata/schema caching.

These pin the two hot paths that used to dominate admin migration endpoints:
re-executing a migration module just to read its docstring, and re-issuing the
whole idempotent schema DDL on every tracker read.
"""

from pathlib import Path

import pytest

from src.core.migrations import tracker as tracker_module
from src.core.migrations.tracker import MigrationTracker


SENTINEL = "SENTINEL_CREATED"


def _install_fake_migrations_dir(monkeypatch, tmp_path: Path) -> Path:
    """Point the tracker module at a temporary migrations directory."""
    fake_module = tmp_path / "tracker.py"
    fake_module.touch()
    monkeypatch.setattr(tracker_module, "__file__", str(fake_module))
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    return migrations_dir


@pytest.mark.migrations
class TestMetadataParsingCache:
    def test_metadata_parsed_without_executing_module(self, monkeypatch, tmp_path, db):
        migrations_dir = _install_fake_migrations_dir(monkeypatch, tmp_path)
        sentinel = tmp_path / SENTINEL
        source = (
            '"""Irreversible migration.\n\n'
            "MIGRATION_METADATA:\n"
            '{"irreversible": true, "depends_on": ["001"]}\n'
            '"""\n'
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).touch()  # must never run for metadata reads\n"
            "def up(db): pass\n"
            "def down(db): pass\n"
        )
        (migrations_dir / "007.py").write_text(source)
        tracker_module._clear_metadata_cache()

        metadata = MigrationTracker(db).get_migration_metadata("007")

        assert metadata["irreversible"] is True
        assert metadata["depends_on"] == ["001"]
        assert not sentinel.exists(), "metadata read executed the migration module"

    def test_metadata_is_cached_until_file_changes(self, monkeypatch, tmp_path, db):
        migrations_dir = _install_fake_migrations_dir(monkeypatch, tmp_path)
        target = migrations_dir / "008.py"
        target.write_text(
            '"""\nMIGRATION_METADATA:\n{"irreversible": false}\n"""\n'
            "def up(db): pass\ndef down(db): pass\n"
        )
        tracker_module._clear_metadata_cache()

        calls = []
        original = tracker_module.validator.migration_metadata_from_docstring

        def counting(docstring):
            calls.append(docstring)
            return original(docstring)

        monkeypatch.setattr(
            tracker_module.validator, "migration_metadata_from_docstring", counting
        )

        tracker = MigrationTracker(db)
        first = tracker.get_migration_metadata("008")
        second = tracker.get_migration_metadata("008")
        assert first == second
        assert len(calls) == 1, "unchanged file was parsed more than once"

        target.write_text(
            '"""\nMIGRATION_METADATA:\n{"irreversible": true}\n"""\n'
            "def up(db): pass\ndef down(db): pass\n"
        )
        updated = MigrationTracker(db).get_migration_metadata("008")
        assert updated["irreversible"] is True
        assert len(calls) == 2, "mtime change did not invalidate the cache"


class _CountingDB:
    """Minimal DB double that counts CREATE statements."""

    type = "sqlite"

    def __init__(self, path: str):
        self.config = {"type": "sqlite", "path": path}
        self.create_calls = 0
        self.last_insert_id = 1

    def execute(self, sql, params=None):
        if "CREATE" in sql.upper():
            self.create_calls += 1
        return None

    def fetch_last_insert_id(self):
        return self.last_insert_id

    def fetch_one(self, *args, **kwargs):
        return None

    def fetch_all(self, *args, **kwargs):
        return None


@pytest.mark.migrations
class TestSchemaSetupCache:
    def test_schema_ddl_issued_once_per_database(self, tmp_path):
        db = _CountingDB(str(tmp_path / "schema_once.db"))
        monkeypatch_schema = dict(MigrationTracker._schema_ready)
        try:
            MigrationTracker._schema_ready = {}
            first = MigrationTracker(db)
            first.ensure_table_exists()
            initial = db.create_calls
            assert initial > 0

            # A fresh tracker over the same database must skip all DDL...
            second = MigrationTracker(db)
            second.ensure_table_exists()
            assert db.create_calls == initial

            # ...but still refresh the uptime session id per instance.
            assert second._current_uptime_session_id == first._current_uptime_session_id
        finally:
            MigrationTracker._schema_ready = monkeypatch_schema

    def test_in_memory_database_always_recreates_schema(self):
        db = _CountingDB(":memory:")
        tracker = MigrationTracker(db)
        tracker.ensure_table_exists()
        first = db.create_calls
        assert first > 0
        tracker.ensure_table_exists()
        assert db.create_calls > first
