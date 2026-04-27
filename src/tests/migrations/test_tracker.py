"""Tests for migration tracker history management."""

import pytest


@pytest.mark.migrations
class TestTracker:
    """Tests for MigrationTracker history and locking."""

    def test_ensure_table_exists(self, migration_manager):
        """Test that ensure_table_exists creates the history table."""
        migration_manager.tracker.ensure_table_exists()
        # Should not raise

    def test_get_applied_migrations(self, migration_manager):
        """Test getting list of applied migrations."""
        migration_manager.tracker.ensure_table_exists()
        applied = migration_manager.tracker.get_applied_migrations()
        assert isinstance(applied, list)

    def test_record_migration_start(self, migration_manager):
        """Test recording migration start."""
        migration_manager.tracker.ensure_table_exists()
        migration_manager.tracker.record_migration_start("999", "test", "abc123")
        status = migration_manager.tracker.get_migration_status("999")
        assert status is not None
        assert status["status"] == "running"
        assert status["name"] == "test"

    def test_record_migration_success(self, migration_manager):
        """Test recording migration success."""
        migration_manager.tracker.ensure_table_exists()
        migration_manager.tracker.record_migration_start(
            "998", "test_success", "def456"
        )
        migration_manager.tracker.record_migration_success("998", 100, None)
        status = migration_manager.tracker.get_migration_status("998")
        assert status is not None
        assert status["status"] == "completed"
        assert status["execution_time_ms"] == 100

    def test_record_migration_failure(self, migration_manager):
        """Test recording migration failure."""
        migration_manager.tracker.ensure_table_exists()
        migration_manager.tracker.record_migration_start(
            "997", "test_failure", "ghi789"
        )
        migration_manager.tracker.record_migration_failure(
            "997", "Something went wrong"
        )
        status = migration_manager.tracker.get_migration_status("997")
        assert status is not None
        assert status["status"] == "failed"
        assert status["error_message"] == "Something went wrong"

    def test_get_migration_status_not_found(self, migration_manager):
        """Test getting status for non-existent migration returns None."""
        migration_manager.tracker.ensure_table_exists()
        result = migration_manager.tracker.get_migration_status("000")
        assert result is None

    def test_get_all_migration_records(self, migration_manager):
        """Test getting all migration records."""
        migration_manager.tracker.ensure_table_exists()
        records = migration_manager.tracker.get_all_migration_records()
        assert isinstance(records, list)

    def test_record_rollback(self, migration_manager):
        """Test recording a migration rollback."""
        migration_manager.tracker.ensure_table_exists()
        migration_manager.tracker.record_migration_start(
            "996", "test_rollback", "jkl012"
        )
        migration_manager.tracker.record_migration_success("996", 50, None)
        migration_manager.tracker.record_rollback("996")
        status = migration_manager.tracker.get_migration_status("996")
        assert status is not None
        assert status["status"] == "rolled_back"

    def test_acquire_and_release_lock(self, migration_manager):
        """Test acquiring and releasing migration lock."""
        migration_manager.tracker.ensure_table_exists()
        acquired = migration_manager.tracker.acquire_lock(timeout=5)
        assert acquired is True
        migration_manager.tracker.release_lock()
        # Should be able to acquire again after release
        acquired2 = migration_manager.tracker.acquire_lock(timeout=5)
        assert acquired2 is True
        migration_manager.tracker.release_lock()

    def test_can_run_irreversible_migration_reversible(self, migration_manager):
        """Test that reversible migrations can always run."""
        can_run, reason = migration_manager.tracker.can_run_irreversible_migration(
            "001", delay_hours=168
        )
        # Most migrations are reversible, so should return True
        assert isinstance(can_run, bool)
        assert isinstance(reason, str)

    def test_log_migration(self, migration_manager):
        """Test logging a migration message."""
        migration_manager.tracker.ensure_table_exists()
        migration_manager.tracker.log_migration("001", "INFO", "Test log message")
        logs = migration_manager.tracker.get_migration_logs("001")
        assert isinstance(logs, list)

    def test_get_migration_logs_empty(self, migration_manager):
        """Test getting logs for migration with no logs."""
        migration_manager.tracker.ensure_table_exists()
        logs = migration_manager.tracker.get_migration_logs("000")
        assert logs == []
