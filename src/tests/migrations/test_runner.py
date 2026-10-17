"""Tests for migration runner execution."""

import pytest
import tempfile
import os

from src.core.migrations.runner import MigrationRunner


@pytest.mark.migrations
class TestRunner:
    """Tests for MigrationRunner execution logic."""

    def test_load_migration_module_valid(self, migration_manager):
        """Test loading a valid migration module."""
        # Find a real migration file
        pending = migration_manager.get_pending_migrations()
        if pending:
            runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
            module = runner.load_migration_module(pending[0].file_path)
            assert hasattr(module, "up")

    def test_load_migration_module_invalid_path(self, migration_manager):
        """Test loading from invalid path raises ImportError."""
        runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
        with pytest.raises(ImportError):
            runner.load_migration_module("/nonexistent/path/migration.py")

    def test_execute_migration_up_missing_up(self, migration_manager):
        """Test that module without up() raises AttributeError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("# Migration without up()\ndef down(db): pass\n")
            f.flush()
            runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
            module = runner.load_migration_module(f.name)
            with pytest.raises(AttributeError, match="missing up"):
                runner.execute_migration_up(module, "999", "test")
        os.unlink(f.name)

    def test_execute_migration_down_missing_down(self, migration_manager):
        """Test that module without down() raises AttributeError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("# Migration without down()\ndef up(db): pass\n")
            f.flush()
            runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
            module = runner.load_migration_module(f.name)
            with pytest.raises(AttributeError, match="no down"):
                runner.execute_migration_down(module, "999")
        os.unlink(f.name)

    def test_validate_migration_file_missing(self, migration_manager):
        """Test validating a missing migration file."""
        runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
        with pytest.raises(FileNotFoundError):
            runner.validate_migration_file("/nonexistent/migration.py")

    def test_runner_dry_run_mode(self, migration_manager):
        """Test that dry_run mode is properly stored."""
        runner = MigrationRunner(
            migration_manager.db, migration_manager.tracker, dry_run=True
        )
        assert runner.dry_run is True

    def test_runner_default_not_dry_run(self, migration_manager):
        """Test that default mode is not dry run."""
        runner = MigrationRunner(migration_manager.db, migration_manager.tracker)
        assert runner.dry_run is False
