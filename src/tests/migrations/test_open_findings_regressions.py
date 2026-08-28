"""
Regression tests for migration cluster fixes.

Covers:
- validate_sql_safety runs on the execution path (M5), not just in tests
- Cloner batch reads are deterministic, tracking tables are excluded, and
  verify_counts catches content drift beyond row counts (M6)
- Migration status cache key is scoped per database instance (M8)
"""

import tempfile
from pathlib import Path

import pytest


def _write_migration(tmpdir, filename, source):
    path = Path(tmpdir) / filename
    path.write_text(source)
    return path


@pytest.mark.migrations
class TestMigrationSqlScreening:
    """M5: destructive-SQL screening must fire during execution."""

    def test_dangerous_up_blocks_execution(
        self, migration_manager, temp_migrations_dir
    ):
        from src.core.migrations.runner import MigrationRunner

        migration_file = _write_migration(
            temp_migrations_dir,
            "099_dangerous.py",
            '''\ndef up(db):
    """Dangerous migration."""
    db.execute("DROP SCHEMA public CASCADE")


def down(db):
    """Rollback."""
    db.execute("DROP TABLE IF EXISTS x")
''',
        )
        runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
        module = runner.load_migration_module(str(migration_file))
        with pytest.raises(ValueError, match="Dangerous SQL"):
            runner.execute_migration_up(module, "099", "dangerous")

    def test_benign_migration_executes(self, migration_manager, temp_migrations_dir):
        from src.core.migrations.runner import MigrationRunner

        migration_file = _write_migration(
            temp_migrations_dir,
            "098_benign.py",
            '''\ndef up(db):
    """Benign migration."""
    db.execute("CREATE TABLE IF NOT EXISTS screen_ok (id INTEGER PRIMARY KEY)")


def down(db):
    """Rollback."""
    db.execute("DROP TABLE IF EXISTS screen_ok")
''',
        )
        runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
        module = runner.load_migration_module(str(migration_file))
        result = runner.execute_migration_up(module, "098", "benign")
        assert result["success"] is True

    def test_dangerous_down_blocks_rollback(
        self, migration_manager, temp_migrations_dir
    ):
        from src.core.migrations.runner import MigrationRunner

        migration_file = _write_migration(
            temp_migrations_dir,
            "097_bad_down.py",
            '''\ndef up(db):
    """Up."""
    db.execute("CREATE TABLE IF NOT EXISTS bad_down (id INTEGER PRIMARY KEY)")


def down(db):
    """Rollback."""
    db.execute("TRUNCATE TABLE migrations_history")
''',
        )
        runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
        module = runner.load_migration_module(str(migration_file))
        with pytest.raises(ValueError, match="Dangerous SQL"):
            runner.execute_migration_down(module, "097")


@pytest.mark.migrations
class TestClonerCorrectness:
    """M6: deterministic reads, tracking exclusions, content verification."""

    def test_tracking_tables_excluded_from_discovery(self):
        from src.core.migrations.cloner import DataCloner

        source = _temp_sqlite_db()
        target = _temp_sqlite_db()
        cloner = DataCloner(source, target)
        for table in (
            "migrations_history",
            "migration_logs",
            "emergency_override_tokens",
        ):
            source.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, note TEXT)")
        tables = cloner.get_tables()
        assert "migrations_history" not in tables
        assert "migration_logs" not in tables
        assert "emergency_override_tokens" not in tables
        source.close()
        target.close()

    def test_clone_orders_by_key_and_verifies_content(self):
        from src.core.migrations.cloner import DataCloner

        source = _temp_sqlite_db()
        target = _temp_sqlite_db()
        source.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")
        target.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")
        source.execute("INSERT INTO items (id, label) VALUES (1, 'a')")
        source.execute("INSERT INTO items (id, label) VALUES (2, 'b')")
        source.execute("INSERT INTO items (id, label) VALUES (3, 'c')")
        cloner = DataCloner(source, target)
        result = cloner.clone_table("items")
        assert result["rows"] == 3
        verification = cloner.verify_counts()
        assert verification["valid"] is True
        source.close()
        target.close()

    def test_verify_counts_catches_content_drift(self):
        from src.core.migrations.cloner import DataCloner

        source = _temp_sqlite_db()
        target = _temp_sqlite_db()
        source.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")
        target.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")
        source.execute("INSERT INTO items (id, label) VALUES (1, 'a')")
        source.execute("INSERT INTO items (id, label) VALUES (2, 'b')")
        target.execute("INSERT INTO items (id, label) VALUES (1, 'a')")
        target.execute("INSERT INTO items (id, label) VALUES (2, 'CHANGED')")
        cloner = DataCloner(source, target)
        cloner._stats["items"] = {"rows": 2, "status": "completed"}
        verification = cloner.verify_counts()
        assert verification["valid"] is False
        assert len(verification["mismatches"]) == 1
        assert verification["mismatches"][0]["content_match"] is False
        source.close()
        target.close()


@pytest.mark.migrations
class TestStatusCacheKeyScoped:
    """M8: the cached status key must be per database instance."""

    def test_keys_differ_across_databases(self):
        from src.core.migrations.manager import (
            MigrationManager,
            _migration_status_cache_key,
        )

        db1 = _temp_sqlite_db()
        db2 = _temp_sqlite_db()
        m1 = MigrationManager(db1)
        m2 = MigrationManager(db2)
        key1 = _migration_status_cache_key(m1)
        key2 = _migration_status_cache_key(m2)
        assert key1 != key2
        assert key1.startswith("migration_status:")
        # Same database yields the same key every time.
        assert _migration_status_cache_key(m1) == key1
        db1.close()
        db2.close()


def _temp_sqlite_db():
    """Create a fresh temporary SQLite Database wired for direct SQL use."""
    import sqlite3

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row

    class _TempDB:
        type = "sqlite"

        def __init__(self, conn, path):
            self._conn = conn
            self.config = {"type": "sqlite", "path": path}

        def execute(self, sql, params=()):
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

        def execute_many(self, sql, seq):
            self._conn.executemany(sql, seq)

        def begin_transaction(self):
            self._conn.execute("BEGIN")

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        def fetch_one(self, sql, params=()):
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

        def fetch_all(self, sql, params=()):
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

        def close(self):
            self._conn.close()

    return _TempDB(conn, tmp.name)
