"""Regression tests for M3: depends_on gate bypass.

- A normal (reversible) migration without a Depends: header still validates.
- An irreversible migration (MIGRATION_METADATA {"irreversible": true},
  the codebase's canonical marker) without depends_on is rejected.
- The irreversible/uptime gate in apply_migration runs independently of
  depends_on (pinned so the bypass cannot silently return).
"""

import inspect

import pytest

from src.core.migrations.manager import Migration, MigrationManager


def _write(path, source):
    path.write_text(source)
    return str(path)


REVERSIBLE_SOURCE = '''"""Reversible migration."""

def up(db):
    db.execute("CREATE TABLE IF NOT EXISTS m3_ok (id INTEGER PRIMARY KEY)")


def down(db):
    db.execute("DROP TABLE IF EXISTS m3_ok")
'''

IRREVERSIBLE_SOURCE = '''"""Irreversible migration.

MIGRATION_METADATA:
{"irreversible": true}
"""

def up(db):
    db.execute("CREATE INDEX IF NOT EXISTS m3_idx ON m3_ok(id)")
'''


@pytest.mark.migrations
class TestM3DependsOnGate:
    def test_reversible_without_depends_still_validates(
        self, migration_manager, temp_migrations_dir
    ):
        file_path = _write(temp_migrations_dir / "090_reversible.py", REVERSIBLE_SOURCE)
        migration = Migration(
            version="090",
            name="reversible",
            file_path=file_path,
            checksum="abc",
            depends_on=[],
        )
        assert migration_manager.validate_dependencies(migration, ["001"]) is True

    def test_irreversible_without_depends_rejected(
        self, migration_manager, temp_migrations_dir
    ):
        file_path = _write(
            temp_migrations_dir / "091_irreversible.py", IRREVERSIBLE_SOURCE
        )
        migration = Migration(
            version="091",
            name="irreversible",
            file_path=file_path,
            checksum="abc",
            depends_on=[],
        )
        with pytest.raises(ValueError, match="irreversible.*depends_on"):
            migration_manager.validate_dependencies(migration, ["001"])

    def test_irreversible_with_depends_passes_gate_shape(
        self, migration_manager, temp_migrations_dir
    ):
        file_path = _write(
            temp_migrations_dir / "092_irreversible.py", IRREVERSIBLE_SOURCE
        )
        migration = Migration(
            version="092",
            name="irreversible",
            file_path=file_path,
            checksum="abc",
            depends_on=["001"],
        )
        # Dependency present and satisfied -> ordering check passes.
        assert migration_manager.validate_dependencies(migration, ["001"]) is True
        # Dependency present but unsatisfied -> still rejected.
        with pytest.raises(ValueError, match="depends on"):
            migration_manager.validate_dependencies(migration, ["000"])

    def test_irreversible_gate_independent_of_depends_on(self):
        """Pin: apply_migration calls can_run_irreversible_migration
        unconditionally (never guarded by a depends_on check)."""
        source = inspect.getsource(MigrationManager.apply_migration)
        assert "can_run_irreversible_migration" in source
        gate_index = source.index("can_run_irreversible_migration")
        head = source[:gate_index]
        assert "if migration.depends_on" not in head
        assert "if depends_on" not in head
