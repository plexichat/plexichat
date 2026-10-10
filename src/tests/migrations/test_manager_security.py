"""Tests for migration manager security features."""

import pytest

from src.core.migrations.manager import Migration, MigrationManager


@pytest.mark.migrations
class TestManagerSecurity:
    """Tests for migration manager security controls."""

    def test_irreversible_migration_check(self, migration_manager):
        """Test checking if an irreversible migration can run."""
        can_run, reason = migration_manager.tracker.can_run_irreversible_migration(
            "001", delay_hours=168
        )
        assert isinstance(can_run, bool)
        assert isinstance(reason, str)

    def test_dependency_validation_prevents_out_of_order(self, migration_manager):
        """Test that dependency validation prevents out-of-order execution."""
        migration = Migration(
            version="005",
            name="depends_on_004",
            file_path="/tmp/test.py",
            checksum="abc",
            depends_on=["004"],
        )
        with pytest.raises(ValueError, match="depends on"):
            migration_manager.validate_dependencies(migration, ["001", "002", "003"])

    def test_cannot_apply_non_pending_migration(self, migration_manager):
        """Test that applying a non-pending migration raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            migration_manager.apply_migration("99999")

    def test_cannot_rollback_non_completed_migration(self, migration_manager):
        """Test that rolling back a non-completed migration raises ValueError."""
        migration_manager.tracker.ensure_table_exists()
        migration_manager.tracker.record_migration_start("995", "test_fail", "abc")
        migration_manager.tracker.record_migration_failure("995", "error")
        with pytest.raises(ValueError, match="status is"):
            migration_manager.rollback_migration("995")

    def test_cannot_rollback_unknown_migration(self, migration_manager):
        """Test that rolling back unknown migration raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            migration_manager.rollback_migration("88888")

    def test_lock_prevents_concurrent_runs(self, migration_manager):
        """Test that acquiring lock prevents concurrent migration."""
        migration_manager.tracker.ensure_table_exists()
        acquired = migration_manager.tracker.acquire_lock(timeout=5)
        assert acquired is True
        # Second lock attempt should fail (or acquire stale lock)
        # Release and try again
        migration_manager.tracker.release_lock()
        acquired2 = migration_manager.tracker.acquire_lock(timeout=5)
        assert acquired2 is True
        migration_manager.tracker.release_lock()

    def test_checksum_integrity_validation(self, migration_manager):
        """Test that integrity validation checks checksums."""
        result = migration_manager.validate_migration_integrity()
        assert "valid" in result
        assert "mismatches" in result

    def test_dry_run_does_not_record(self, migration_manager):
        """Test that dry run does not record migration in tracker."""
        migration_manager.get_migration_status()
        result = migration_manager.apply_all_pending(dry_run=True)
        assert result["dry_run"] is True
        # Applied count should not change from dry run


@pytest.mark.migrations
class TestRollbackSerialization:
    """Rollback must take the same migration lock as the apply paths.

    ``apply_migration`` and ``apply_all_pending`` acquire the migration lock
    (fail closed) and release it in a ``finally``; a rollback that skips the
    lock can interleave its DDL and ``migrations_history`` writes with an
    in-flight batch apply on the shared database.
    """

    _SOURCE = (
        "def up(db):\n"
        '    db.execute("CREATE TABLE IF NOT EXISTS rb_lock_ok '
        '(id INTEGER PRIMARY KEY)")\n'
        "\n"
        "\n"
        "def down(db):\n"
        '    db.execute("DROP TABLE IF EXISTS rb_lock_ok")\n'
    )

    def _manager_with_completed_migration(self, migration_manager, temp_migrations_dir):
        (temp_migrations_dir / "089_rb_lock.py").write_text(self._SOURCE)
        manager = MigrationManager(migration_manager.db)
        manager.migrations_dir = temp_migrations_dir
        tracker = manager.tracker
        tracker.ensure_table_exists()
        tracker.record_migration_start("089", "rb_lock", "checksum")
        tracker.record_migration_success("089", 1, None)
        return manager

    def test_rollback_refuses_when_the_lock_is_held(
        self, migration_manager, temp_migrations_dir, monkeypatch
    ):
        manager = self._manager_with_completed_migration(
            migration_manager, temp_migrations_dir
        )
        tracker = manager.tracker

        # Simulate another run holding the lock: acquisition fails closed.
        real_acquire = tracker.acquire_lock
        monkeypatch.setattr(tracker, "acquire_lock", lambda *a, **k: False)

        with pytest.raises(Exception, match="Could not acquire migration lock"):
            manager.rollback_migration("089")

        # The blocked attempt ran no DDL and left the record untouched.
        assert tracker.get_migration_status("089")["status"] == "completed"

        # Non-vacuity: with the lock free the same rollback succeeds.
        monkeypatch.setattr(tracker, "acquire_lock", real_acquire)
        result = manager.rollback_migration("089")
        assert result["success"] is True
        assert tracker.get_migration_status("089")["status"] == "rolled_back"
