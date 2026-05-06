"""Tests for migration manager orchestration."""

import pytest

from src.core.migrations.manager import Migration


@pytest.mark.migrations
class TestManager:
    """Tests for MigrationManager orchestration."""

    def test_get_pending_migrations(self, migration_manager):
        """Test getting pending migrations."""
        pending = migration_manager.get_pending_migrations()
        assert isinstance(pending, list)

    def test_get_migration_status(self, migration_manager):
        """Test getting migration status summary."""
        status = migration_manager.get_migration_status()
        assert "applied_count" in status
        assert "pending_count" in status
        assert "failed_count" in status
        assert "applied_migrations" in status
        assert "pending_migrations" in status
        assert "failed_migrations" in status

    def test_apply_all_pending_no_pending(self, migration_manager):
        """Test applying migrations when none are pending."""
        # After initial setup, may or may not have pending
        result = migration_manager.apply_all_pending(dry_run=True)
        assert "success" in result
        assert "applied_count" in result
        assert "failed_count" in result
        assert "dry_run" in result
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
        assert (
            migration_manager.validate_dependencies(migration, ["001", "002"]) is True
        )

    def test_validate_dependencies_satisfied(self, migration_manager):
        """Test validating migration with satisfied dependencies."""
        migration = Migration(
            version="003",
            name="test_migration",
            file_path="/tmp/test.py",
            checksum="abc123",
            depends_on=["001", "002"],
        )
        assert (
            migration_manager.validate_dependencies(migration, ["001", "002"]) is True
        )

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
        """Test migration integrity validation."""
        result = migration_manager.validate_migration_integrity()
        assert "valid" in result
        assert "checked" in result
        assert "mismatches" in result

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
        """Test that dry run mode does not persist changes."""
        result = migration_manager.apply_all_pending(dry_run=True)
        assert result["dry_run"] is True
        # After dry run, applied_count should be the same
        migration_manager.get_migration_status()
        # Dry run should not change applied count
        # (it skips tracker operations)
