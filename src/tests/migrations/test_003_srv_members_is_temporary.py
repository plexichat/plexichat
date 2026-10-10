"""
Tests for migration 003: srv_members.is_temporary column.

Covers the ALTER (with idempotent duplicate-column handling on fresh
installations) and the ``down`` rollback. The missing column broke every
server join on databases created before the temporary-membership feature
(``table srv_members has no column named is_temporary``), which stranded
invite joins with a 500 and cascaded into membership-dependent failures.
"""

import importlib.util
from pathlib import Path


def _load_migration():
    path = Path("src/core/migrations/migrations/003_srv_members_is_temporary.py")
    spec = importlib.util.spec_from_file_location(
        "migration_003_srv_members_is_temporary", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(db):
    return {row["name"] for row in db.fetch_all("PRAGMA table_info(srv_members)")}


def test_003_up_adds_column_to_legacy_schema(db):
    migration = _load_migration()

    # Simulate a pre-003 database: no is_temporary column yet.
    db.execute("ALTER TABLE srv_members DROP COLUMN is_temporary")
    assert "is_temporary" not in _columns(db)

    migration.up(db)

    assert "is_temporary" in _columns(db)


def test_003_up_is_idempotent_on_fresh_schema(db):
    """Fresh installs already have the column (module schema is authoritative)."""
    migration = _load_migration()
    assert "is_temporary" in _columns(db)
    # The duplicate-column ALTER must not raise.
    migration.up(db)
    assert "is_temporary" in _columns(db)


def test_003_down_drops_the_column(db):
    migration = _load_migration()
    migration.down(db)
    assert "is_temporary" not in _columns(db)
    # And up() can re-add it.
    migration.up(db)
    assert "is_temporary" in _columns(db)


def test_003_membership_insert_works_after_up(db):
    """The add_member INSERT shape must succeed on a migrated legacy schema."""
    import time

    migration = _load_migration()
    db.execute("ALTER TABLE srv_members DROP COLUMN is_temporary")
    migration.up(db)

    now = int(time.time() * 1000)
    # Parent rows are out of scope for this migration test; relax FK
    # enforcement for the probe insert only.
    db.execute("PRAGMA foreign_keys=OFF")
    try:
        db.execute(
            """INSERT INTO srv_members
               (id, server_id, user_id, joined_at, updated_at, inviter_id, is_temporary)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (987654321, 111, 222, now, now, None, 0),
        )
    finally:
        db.execute("PRAGMA foreign_keys=ON")
    row = db.fetch_one("SELECT * FROM srv_members WHERE id = ?", (987654321,))
    assert row is not None
    assert int(row["is_temporary"]) == 0
