"""
Tests for the migration tracker.
"""

from src.core.migrations.tracker import MigrationTracker


class TestMigrationTracker:
    """Test MigrationTracker class."""

    def test_ensure_table_exists(self, test_db):
        """Test that migrations_history table is created."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        # Table should exist now
        result = test_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='migrations_history'"
        )
        assert result is not None and len(result) > 0

    def test_get_applied_migrations_empty(self, test_db):
        """Test getting applied migrations when none exist."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        applied = tracker.get_applied_migrations()
        assert applied == []

    def test_record_migration_start(self, test_db):
        """Test recording a migration start."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        tracker.record_migration_start("001", "test migration", "abc123")

        status = tracker.get_migration_status("001")
        assert status is not None
        assert status["version"] == "001"
        assert status["name"] == "test migration"
        assert status["status"] == "running"
        assert status["checksum"] == "abc123"

    def test_record_migration_success(self, test_db):
        """Test recording migration success."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        tracker.record_migration_start("001", "test migration", "abc123")
        tracker.record_migration_success("001", 100, "rollback sql")

        status = tracker.get_migration_status("001")
        assert status["status"] == "completed"
        assert status["execution_time_ms"] == 100
        assert status["rollback_sql"] == "rollback sql"

    def test_record_migration_failure(self, test_db):
        """Test recording migration failure."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        tracker.record_migration_start("001", "test migration", "abc123")
        tracker.record_migration_failure("001", "Test error message")

        status = tracker.get_migration_status("001")
        assert status["status"] == "failed"
        assert status["error_message"] == "Test error message"

    def test_get_all_migration_records(self, test_db):
        """Test retrieving all migration records."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        # Add multiple migrations
        tracker.record_migration_start("001", "first", "hash1")
        tracker.record_migration_success("001", 50)

        tracker.record_migration_start("002", "second", "hash2")
        tracker.record_migration_failure("002", "error")

        records = tracker.get_all_migration_records()
        assert len(records) == 2
        assert records[0]["version"] == "001"
        assert records[1]["version"] == "002"
        assert records[0]["status"] == "completed"
        assert records[1]["status"] == "failed"

    def test_record_rollback(self, test_db):
        """Test recording a rollback."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        tracker.record_migration_start("001", "test", "hash1")
        tracker.record_migration_success("001", 50)
        tracker.record_rollback("001")

        status = tracker.get_migration_status("001")
        assert status["status"] == "rolled_back"

    def test_get_migration_status_not_found(self, test_db):
        """Test getting status of non-existent migration."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        status = tracker.get_migration_status("999")
        assert status is None

    def test_applied_migrations_query_format(self, test_db):
        """Test that applied migrations are returned as list of strings."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        tracker.record_migration_start("001", "test1", "hash1")
        tracker.record_migration_success("001", 50)

        tracker.record_migration_start("002", "test2", "hash2")
        tracker.record_migration_failure("002", "error")

        applied = tracker.get_applied_migrations()
        # Only completed migrations should be in applied list
        assert len(applied) == 1
        assert applied[0] == "001"
        assert isinstance(applied[0], str)

    def test_record_migration_start_idempotent_for_failed(self, test_db):
        """Test that recording a failed migration can be retried."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        # Record initial failed migration
        tracker.record_migration_start("001", "test", "hash1")
        tracker.record_migration_failure("001", "First attempt failed")

        status = tracker.get_migration_status("001")
        assert status["status"] == "failed"
        assert status["error_message"] == "First attempt failed"

        # Record migration start again with different checksum (simulating retry)
        # Should use INSERT OR REPLACE to update the existing row
        tracker.record_migration_start("001", "test", "hash2_updated")

        status = tracker.get_migration_status("001")
        assert status["status"] == "running"
        assert status["checksum"] == "hash2_updated"

        # Verify only one row exists
        all_records = tracker.get_all_migration_records()
        assert len(all_records) == 1

    def test_record_migration_start_preserves_created_at(self, test_db):
        """Test that created_at is preserved when retrying a migration."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()

        # Record initial migration
        tracker.record_migration_start("001", "test", "hash1")
        first_record = tracker.get_migration_status("001")
        first_created_at = first_record["created_at"]

        # Record migration start again (retry)
        tracker.record_migration_start("001", "test", "hash2")
        second_record = tracker.get_migration_status("001")
        second_created_at = second_record["created_at"]

        # created_at should be the same
        assert first_created_at == second_created_at
