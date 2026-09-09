"""Tests for migration manager orchestration."""

import pytest

from src.core.migrations.manager import Migration


@pytest.mark.migrations
class TestManager:
    """Tests for MigrationManager orchestration."""

    def test_get_pending_migrations(self, migration_manager):
        """The migrated template has both migrations applied; nothing pending."""
        pending = migration_manager.get_pending_migrations()
        assert pending == []

    def test_get_migration_status(self, migration_manager):
        """Status reflects the three template migrations as applied."""
        from src.core.database import invalidate_pattern

        # get_migration_status is @cached with a manager-agnostic key, so a
        # stale entry from an earlier test in the same worker would otherwise
        # describe a different test's database.
        invalidate_pattern("migration_status:*")
        status = migration_manager.get_migration_status()
        assert status["applied_count"] == 3
        assert status["pending_count"] == 0
        assert status["failed_count"] == 0
        assert set(status["applied_migrations"]) == {"000", "001", "002"}
        assert status["pending_migrations"] == []
        assert status["failed_migrations"] == []

    def test_apply_all_pending_no_pending(self, migration_manager):
        """Nothing pending means a zero-migration result, not a fake success."""
        result = migration_manager.apply_all_pending(dry_run=True)
        assert result["success"] is True
        assert result["applied_count"] == 0
        assert result["failed_count"] == 0
        assert result["migrations"] == []
        assert result["dry_run"] is True

    def test_apply_migration_not_found(self, migration_manager):
        """Test applying a non-existent migration raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            migration_manager.apply_migration("99999")

    def test_validate_dependencies_no_deps(self, migration_manager):
        """Test validating migration with no dependencies."""
        migration = Migration(
            version="999",
            name="test_migration",
            file_path="/tmp/test.py",
            checksum="abc123",
            depends_on=[],
        )
        assert migration_manager.validate_dependencies(migration, ["001"]) is True

    def test_validate_dependencies_satisfied(self, migration_manager):
        """Test validating migration with satisfied dependencies."""
        migration = Migration(
            version="003",
            name="test_migration",
            file_path="/tmp/test.py",
            checksum="abc123",
            depends_on=["001"],
        )
        assert migration_manager.validate_dependencies(migration, ["001"]) is True

    def test_validate_dependencies_unsatisfied(self, migration_manager):
        """Test validating migration with unsatisfied dependencies."""
        migration = Migration(
            version="003",
            name="test_migration",
            file_path="/tmp/test.py",
            checksum="abc123",
            depends_on=["001", "005"],
        )
        with pytest.raises(ValueError, match="depends on"):
            migration_manager.validate_dependencies(migration, ["001", "002"])

    def test_validate_migration_integrity(self, migration_manager):
        """All template migrations pass integrity with matching checksums."""
        result = migration_manager.validate_migration_integrity()
        assert result["valid"] is True
        assert result["checked"] == 3  # 000 + 001 + 002 completed records
        assert result["mismatches"] == []

    def test_migration_dataclass(self):
        """Test Migration dataclass fields."""
        m = Migration(version="001", name="test", file_path="/tmp/test.py")
        assert m.version == "001"
        assert m.name == "test"
        assert m.checksum == ""
        assert m.depends_on == []

    def test_rollback_nonexistent_migration(self, migration_manager):
        """Test rolling back a non-existent migration raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            migration_manager.rollback_migration("99999")

    def test_dry_run_does_not_persist(self, migration_manager):
        """A dry run must not write anything to migrations_history."""
        from src.core.database import invalidate_pattern

        invalidate_pattern("migration_status:*")
        tracker = migration_manager.tracker
        tracker.ensure_table_exists()
        before = len(tracker.get_all_migration_records())

        result = migration_manager.apply_all_pending(dry_run=True)

        assert result["dry_run"] is True
        assert result["applied_count"] == 0
        # No "running"/"completed" rows may be created by a dry run.
        after = len(tracker.get_all_migration_records())
        assert after == before
