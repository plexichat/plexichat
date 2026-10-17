"""M5 regression: DROP TABLE / DELETE-without-WHERE / TRUNCATE in a forward
migration (up()) must be rejected by validation and never executed."""

from pathlib import Path

import pytest


def _write_migration(tmpdir, filename, source):
    path = Path(tmpdir) / filename
    path.write_text(source)
    return path


DROP_TABLE_UP = '''\ndef up(db):
    """Forward migration that drops a production table."""
    db.execute("DROP TABLE users")


def down(db):
    """Rollback."""
    db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)")
'''

BARE_DELETE_UP = '''\ndef up(db):
    """Forward migration that deletes without a scope."""
    db.execute("DELETE FROM users")


def down(db):
    """Rollback."""
    db.execute("SELECT 1")
'''

TRUNCATE_UP = '''\ndef up(db):
    """Forward migration that truncates a table."""
    db.execute("TRUNCATE users")


def down(db):
    """Rollback."""
    db.execute("SELECT 1")
'''

SCOPED_DELETE_UP = '''\ndef up(db):
    """Forward migration with a scoped delete."""
    db.execute("DELETE FROM users WHERE id = ?", (1,))


def down(db):
    """Rollback."""
    db.execute("SELECT 1")
'''


class _RecordingDB:
    """Minimal db double proving whether migration SQL ever ran."""

    def __init__(self):
        self.calls = []

    def begin_transaction(self):
        self.calls.append("begin")

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def execute(self, sql, params=()):
        self.calls.append(("execute", sql))
        return None


@pytest.mark.migrations
class TestM5DestructiveUpBlocked:
    """A migration containing DROP TABLE users is rejected, never executed."""

    def test_validate_migration_file_rejects_drop_table(self, temp_migrations_dir):
        from src.core.migrations.validator import validate_migration_file

        path = _write_migration(temp_migrations_dir, "099_drop_users.py", DROP_TABLE_UP)
        with pytest.raises(ValueError, match="DROP TABLE"):
            validate_migration_file(str(path))

    def test_execute_migration_up_rejects_drop_table_and_never_runs(
        self, migration_manager, temp_migrations_dir
    ):
        from src.core.migrations.runner import MigrationRunner

        path = _write_migration(temp_migrations_dir, "099_drop_users.py", DROP_TABLE_UP)
        runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
        module = runner.load_migration_module(str(path))

        db = _RecordingDB()
        runner.db = db
        with pytest.raises(ValueError, match="DROP TABLE"):
            runner.execute_migration_up(module, "099", "drop_users")
        assert db.calls == []

    def test_runner_validate_migration_file_rejects_drop_table(
        self, migration_manager, temp_migrations_dir
    ):
        from src.core.migrations.runner import MigrationRunner

        path = _write_migration(temp_migrations_dir, "099_drop_users.py", DROP_TABLE_UP)
        runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
        with pytest.raises(ValueError, match="DROP TABLE"):
            runner.validate_migration_file(str(path))

    def test_bare_delete_up_blocked(self, temp_migrations_dir):
        from src.core.migrations.validator import validate_migration_up_sql

        path = _write_migration(
            temp_migrations_dir, "098_bare_delete.py", BARE_DELETE_UP
        )
        with pytest.raises(ValueError, match="DELETE without WHERE"):
            validate_migration_up_sql(path.read_text())

    def test_truncate_up_blocked(self, temp_migrations_dir):
        from src.core.migrations.validator import validate_migration_up_sql

        path = _write_migration(temp_migrations_dir, "097_truncate.py", TRUNCATE_UP)
        with pytest.raises(ValueError, match="TRUNCATE"):
            validate_migration_up_sql(path.read_text())

    def test_scoped_delete_up_allowed(self):
        from src.core.migrations.validator import validate_migration_up_sql

        is_safe, _ = validate_migration_up_sql(SCOPED_DELETE_UP)
        assert is_safe is True

    def test_down_drop_table_still_validates(self, temp_migrations_dir):
        """Rollbacks legitimately reverse DDL, so DROP TABLE in down() stays
        valid while up() is clean."""
        from src.core.migrations.validator import validate_migration_file

        path = _write_migration(
            temp_migrations_dir,
            "096_down_drop.py",
            '''\ndef up(db):
    """Create a table."""
    db.execute("CREATE TABLE IF NOT EXISTS tmp_m5 (id INTEGER PRIMARY KEY)")


def down(db):
    """Rollback drops what up() created."""
    db.execute("DROP TABLE IF EXISTS tmp_m5")
''',
        )
        assert validate_migration_file(str(path)) is True
