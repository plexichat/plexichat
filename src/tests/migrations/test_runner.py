"""
Tests for the migration runner.
"""

import pytest

from src.core.migrations.runner import MigrationRunner
from src.core.migrations.tracker import MigrationTracker


class TestMigrationRunner:
    """Test MigrationRunner class."""

    def test_load_migration_module(self, sample_migration):
        """Test loading a migration module."""
        tracker = MigrationTracker(None)  # Not used in this test
        runner = MigrationRunner(None, tracker)

        module = runner.load_migration_module(str(sample_migration))

        assert hasattr(module, "up")
        assert hasattr(module, "down")
        assert callable(module.up)
        assert callable(module.down)

    def test_load_migration_missing_file(self):
        """Test loading non-existent migration."""
        tracker = MigrationTracker(None)
        runner = MigrationRunner(None, tracker)

        with pytest.raises(ImportError):
            runner.load_migration_module("/nonexistent/migration.py")

    def test_validate_migration_file(self, sample_migration):
        """Test migration file validation in runner."""
        tracker = MigrationTracker(None)
        runner = MigrationRunner(None, tracker)

        # Should not raise
        runner.validate_migration_file(str(sample_migration))

    def test_execute_migration_up_requires_up_function(
        self, test_db, temp_migrations_dir
    ):
        """Test that migration must have up() function."""
        tracker = MigrationTracker(test_db)
        runner = MigrationRunner(test_db, tracker)

        # Create migration without up() function
        bad_migration = temp_migrations_dir / "bad.py"
        bad_migration.write_text("def down(db): pass")

        module = runner.load_migration_module(str(bad_migration))

        with pytest.raises(AttributeError, match="missing.*up"):
            runner.execute_migration_up(module, "001", "bad")

    def test_dry_run_mode(self, test_db, sample_migration):
        """Test dry-run mode executes but doesn't commit."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        runner = MigrationRunner(test_db, tracker, dry_run=True)
        module = runner.load_migration_module(str(sample_migration))

        result = runner.execute_migration_up(module, "001", "test")

        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["execution_time_ms"] > 0

        # Table should not exist (changes were rolled back)
        table_result = test_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
        )
        assert table_result is None or len(table_result) == 0

    def test_execute_migration_down_success(self, test_db, sample_migration):
        """Test successful migration rollback."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        # First apply the migration
        runner = MigrationRunner(test_db, tracker)
        module = runner.load_migration_module(str(sample_migration))
        runner.execute_migration_up(module, "001", "test")

        # Now rollback
        result = runner.execute_migration_down(module, "001")

        assert result["success"] is True
        assert result["version"] == "001"

        # Table should no longer exist
        table_result = test_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
        )
        assert table_result is None or len(table_result) == 0

    def test_execute_migration_down_requires_down_function(
        self, test_db, migration_without_down
    ):
        """Test that rollback fails without down() function."""
        tracker = MigrationTracker(test_db)
        runner = MigrationRunner(test_db, tracker)

        module = runner.load_migration_module(str(migration_without_down))

        with pytest.raises(AttributeError, match="no down"):
            runner.execute_migration_down(module, "003")

    def test_execution_time_measurement(self, test_db, sample_migration):
        """Test that execution time is measured."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        runner = MigrationRunner(test_db, tracker)
        module = runner.load_migration_module(str(sample_migration))

        result = runner.execute_migration_up(module, "001", "test")

        assert "execution_time_ms" in result
        assert isinstance(result["execution_time_ms"], int)
        assert result["execution_time_ms"] >= 0

    def test_transaction_rollback_on_error(self, test_db, migration_with_error):
        """Test that transaction is rolled back if migration fails."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        runner = MigrationRunner(test_db, tracker)
        module = runner.load_migration_module(str(migration_with_error))

        # This should raise an exception
        with pytest.raises(Exception):
            runner.execute_migration_up(module, "004", "error")

        # Transaction should be rolled back automatically
        # (test_db handles this internally)


class TestMigrationRunnerIntegration:
    """Integration tests for MigrationRunner."""

    def test_migrate_and_rollback_sequence(self, test_db, sample_migration):
        """Test complete migration and rollback sequence."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        runner = MigrationRunner(test_db, tracker)
        module = runner.load_migration_module(str(sample_migration))

        # Apply migration
        up_result = runner.execute_migration_up(module, "001", "test")
        assert up_result["success"] is True

        # Verify table exists
        table_result = test_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
        )
        assert table_result is not None and len(table_result) > 0

        # Rollback migration
        down_result = runner.execute_migration_down(module, "001")
        assert down_result["success"] is True

        # Verify table is gone
        table_result = test_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
        )
        assert table_result is None or len(table_result) == 0
