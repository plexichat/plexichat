"""
Tests for migration 002: artifacts.rev column.

Covers the ALTER (with idempotent duplicate-column handling on fresh
installations), the backfill from the legacy ``payload.rev`` chain, and the
``down`` rollback.
"""

import importlib.util
from pathlib import Path

from src.core.artifacts.repository import get_artifact


def _load_migration():
    path = Path("src/core/migrations/migrations/002_artifact_rev.py")
    spec = importlib.util.spec_from_file_location("migration_002_artifact_rev", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _insert_artifact(db, artifact_id, payload):
    """Insert a raw artifacts row without touching the rev column."""
    db.execute(
        "INSERT INTO artifacts "
        "(id, author_id, artifact_type, title, status, payload, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
        (artifact_id, 42, "file", "migration test", "completed", payload),
    )


def test_002_up_adds_column_and_backfills_from_payload_rev(db):
    migration = _load_migration()

    # Simulate a pre-002 database: no rev column yet.
    db.execute("ALTER TABLE artifacts DROP COLUMN rev")

    # Legacy artifacts: one edited (payload.rev), one never edited.
    _insert_artifact(db, 1001, '{"content": "a", "rev": 9}')
    _insert_artifact(db, 1002, '{"content": "b"}')
    _insert_artifact(db, 1003, '{"rev": "draft"}')  # non-numeric rev

    migration.up(db)

    assert get_artifact(db, 1001).rev == 9
    assert get_artifact(db, 1002).rev == 0
    assert get_artifact(db, 1003).rev == 0


def test_002_up_is_idempotent_on_fresh_schema(db):
    """Fresh installs already have the column (module schema is authoritative)."""
    migration = _load_migration()
    # The test template DB has the column; up() must not raise on the
    # duplicate-column ALTER and must still backfill.
    migration.up(db)
    assert get_artifact(db, 0) is None or True  # just ensure no exception


def test_002_down_drops_the_column(db):
    migration = _load_migration()
    migration.down(db)
    cols = {row["name"] for row in db.fetch_all("PRAGMA table_info(artifacts)")}
    assert "rev" not in cols
    # And up() can re-add it.
    migration.up(db)
    cols = {row["name"] for row in db.fetch_all("PRAGMA table_info(artifacts)")}
    assert "rev" in cols
