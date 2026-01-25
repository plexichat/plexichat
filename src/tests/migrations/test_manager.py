"""
Tests for the migration manager.
"""

import pytest
from pathlib import Path

from src.core.migrations.manager import MigrationManager, Migration
from src.core.migrations.tracker import MigrationTracker


class TestMigration:
    """Test Migration class."""
    
    def test_migration_creation(self):
        """Test creating a migration object."""
        mig = Migration('001', 'test migration', '/path/to/001_test.py', 'abc123')
        
        assert mig.version == '001'
        assert mig.name == 'test migration'
        assert mig.file_path == '/path/to/001_test.py'
        assert mig.checksum == 'abc123'


class TestMigrationManager:
    """Test MigrationManager class."""
    
    def test_manager_initialization(self, test_db):
        """Test MigrationManager initialization."""
        manager = MigrationManager(test_db)
        
        assert manager.db is test_db
        assert manager.tracker is not None
        assert manager.migrations_dir.exists()
    
    def test_get_pending_migrations_no_migrations(self, test_db):
        """Test getting pending when no migrations exist."""
        manager = MigrationManager(test_db)
        pending = manager.get_pending_migrations()
        
        assert pending == []
    
    def test_get_pending_migrations_with_files(self, test_db, monkeypatch):
        """Test getting pending migrations when files exist."""
        # Create a temporary migrations directory with test files
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir) / 'migrations'
            migrations_dir.mkdir()
            
            # Create migration files
            (migrations_dir / '001_first.py').write_text('def up(db): pass')
            (migrations_dir / '002_second.py').write_text('def up(db): pass')
            
            manager = MigrationManager(test_db)
            # Patch the migrations directory
            monkeypatch.setattr(manager, 'migrations_dir', migrations_dir)
            
            pending = manager.get_pending_migrations()
            
            assert len(pending) == 2
            assert pending[0].version == '001'
            assert pending[1].version == '002'
    
    def test_apply_migration_not_found(self, test_db):
        """Test applying non-existent migration."""
        manager = MigrationManager(test_db)
        
        with pytest.raises(ValueError, match='not found'):
            manager.apply_migration('999')
    
    def test_get_migration_status_summary(self, test_db):
        """Test getting migration status summary."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()
        
        # Add some migrations
        tracker.record_migration_start('001', 'first', 'hash1')
        tracker.record_migration_success('001', 50)
        
        tracker.record_migration_start('002', 'second', 'hash2')
        tracker.record_migration_failure('002', 'error')
        
        manager = MigrationManager(test_db)
        status = manager.get_migration_status()
        
        assert status['applied_count'] == 1
        assert status['failed_count'] == 1
        assert '001' in status['applied_migrations']
        assert '002' in status['failed_migrations']
    
    def test_validate_migration_integrity(self, test_db, monkeypatch):
        """Test migration integrity validation."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()
        
        # Add a valid migration record
        tracker.record_migration_start('001', 'test', 'abc123')
        tracker.record_migration_success('001', 50)
        
        # Create actual migration file with matching content
        import tempfile
        import hashlib
        
        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir) / 'migrations'
            migrations_dir.mkdir()
            
            # Create migration file
            content = b'def up(db): pass'
            checksum = hashlib.sha256(content).hexdigest()
            
            # Update the recorded checksum to match
            tracker.db.execute(
                'UPDATE migrations_history SET checksum = ? WHERE version = ?',
                (checksum, '001')
            )
            
            (migrations_dir / '001_test.py').write_bytes(content)
            
            manager = MigrationManager(test_db)
            monkeypatch.setattr(manager, 'migrations_dir', migrations_dir)
            
            result = manager.validate_migration_integrity()
            
            assert result['valid'] is True
            assert result['checked'] == 1
    
    def test_rollback_migration_not_found(self, test_db):
        """Test rolling back non-existent migration."""
        manager = MigrationManager(test_db)
        
        with pytest.raises(ValueError, match='not found'):
            manager.rollback_migration('999')
    
    def test_rollback_migration_not_completed(self, test_db):
        """Test rolling back migration that wasn't completed."""
        tracker = MigrationTracker(test_db)
        tracker.ensure_table_exists()
        
        tracker.record_migration_start('001', 'test', 'hash1')
        # Don't mark as completed
        
        manager = MigrationManager(test_db)
        
        with pytest.raises(ValueError, match='Cannot rollback'):
            manager.rollback_migration('001')


class TestMigrationManagerApplyAll:
    """Test applying all pending migrations."""
    
    def test_apply_all_pending_no_pending(self, test_db):
        """Test applying when no migrations are pending."""
        manager = MigrationManager(test_db)
        result = manager.apply_all_pending()
        
        assert result['success'] is True
        assert result['applied_count'] == 0
        assert result['failed_count'] == 0
    
    def test_apply_all_pending_with_files(self, test_db, monkeypatch):
        """Test applying all pending migrations."""
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir) / 'migrations'
            migrations_dir.mkdir()
            
            # Create migration files
            (migrations_dir / '001_first.py').write_text('''
def up(db):
    db.execute("""
        CREATE TABLE test_001 (id INTEGER PRIMARY KEY)
    """)
''')
            
            (migrations_dir / '002_second.py').write_text('''
def up(db):
    db.execute("""
        CREATE TABLE test_002 (id INTEGER PRIMARY KEY)
    """)
''')
            
            manager = MigrationManager(test_db)
            monkeypatch.setattr(manager, 'migrations_dir', migrations_dir)
            
            result = manager.apply_all_pending()
            
            assert result['success'] is True
            assert result['applied_count'] == 2
            assert result['failed_count'] == 0
            assert len(result['migrations']) == 2
    
    def test_apply_all_stops_on_failure(self, test_db, monkeypatch):
        """Test that migration stops on first failure."""
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir) / 'migrations'
            migrations_dir.mkdir()
            
            # Create migration that succeeds
            (migrations_dir / '001_first.py').write_text('''
def up(db):
    db.execute("CREATE TABLE test_001 (id INTEGER PRIMARY KEY)")
''')
            
            # Create migration that fails
            (migrations_dir / '002_fail.py').write_text('''
def up(db):
    db.execute("CREATE TABLE nonexistent FROM invalid")
''')
            
            # Create migration that would succeed
            (migrations_dir / '003_third.py').write_text('''
def up(db):
    db.execute("CREATE TABLE test_003 (id INTEGER PRIMARY KEY)")
''')
            
            manager = MigrationManager(test_db)
            monkeypatch.setattr(manager, 'migrations_dir', migrations_dir)
            
            result = manager.apply_all_pending()
            
            assert result['success'] is False
            assert result['applied_count'] == 1  # Only first one succeeded
            assert result['failed_count'] == 1


class TestMigrationManagerDryRun:
    """Test dry-run mode."""
    
    def test_dry_run_doesnt_persist(self, test_db, monkeypatch):
        """Test that dry-run doesn't persist changes."""
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir) / 'migrations'
            migrations_dir.mkdir()
            
            (migrations_dir / '001_test.py').write_text('''
def up(db):
    db.execute("CREATE TABLE test_dry (id INTEGER PRIMARY KEY)")
''')
            
            manager = MigrationManager(test_db)
            monkeypatch.setattr(manager, 'migrations_dir', migrations_dir)
            
            result = manager.apply_all_pending(dry_run=True)
            
            assert result['success'] is True
            assert result['dry_run'] is True
            
            # Table should not exist
            table_result = test_db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='test_dry'"
            )
            assert table_result is None or len(table_result) == 0


class TestMigrationIntegration:
    """Integration tests for migration system."""
    
    def test_complete_migration_workflow(self, test_db, monkeypatch):
        """Test complete migration workflow."""
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir) / 'migrations'
            migrations_dir.mkdir()
            
            # Create a migration
            (migrations_dir / '001_create_users.py').write_text('''
def up(db):
    db.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255)
        )
    """)

def down(db):
    db.execute("DROP TABLE users")
''')
            
            manager = MigrationManager(test_db)
            monkeypatch.setattr(manager, 'migrations_dir', migrations_dir)
            
            # Initially should have pending migrations
            status = manager.get_migration_status()
            assert status['pending_count'] == 1
            assert status['applied_count'] == 0
            
            # Apply migrations
            result = manager.apply_all_pending()
            assert result['success'] is True
            assert result['applied_count'] == 1
            
            # Now should have no pending
            status = manager.get_migration_status()
            assert status['pending_count'] == 0
            assert status['applied_count'] == 1
            
            # Table should exist
            table_result = test_db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            )
            assert table_result is not None and len(table_result) > 0
    
    def test_dry_run_leaves_no_tracker_rows(self, test_db, monkeypatch):
        """Test that dry-run mode does not write to migrations_history."""
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir) / 'migrations'
            migrations_dir.mkdir()
            
            # Create a simple migration
            (migrations_dir / '001_test.py').write_text('''
def up(db):
    db.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY)")
''')
            
            manager = MigrationManager(test_db)
            monkeypatch.setattr(manager, 'migrations_dir', migrations_dir)
            
            # Run in dry-run mode
            result = manager.apply_all_pending(dry_run=True)
            assert result['success'] is True
            assert result['dry_run'] is True
            assert result['applied_count'] == 1
            
            # Check that migrations_history has no rows
            tracker = manager.tracker
            tracker.ensure_table_exists()
            
            all_records = tracker.get_all_migration_records()
            assert len(all_records) == 0, "Dry-run should not write to migrations_history"
            
            # Check that the table was not created (migration was rolled back)
            table_result = test_db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
            )
            assert table_result is None or len(table_result) == 0
            
            # Now do a real run - should succeed without UNIQUE constraint errors
            result = manager.apply_all_pending(dry_run=False)
            assert result['success'] is True
            assert result['applied_count'] == 1
            
            # Table should now exist
            table_result = test_db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
            )
            assert table_result is not None and len(table_result) > 0
    
    def test_failed_migration_can_be_retried(self, test_db, monkeypatch):
        """Test that failed migrations can be retried without UNIQUE constraint errors."""
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir) / 'migrations'
            migrations_dir.mkdir()
            
            # Create a migration that will fail
            (migrations_dir / '001_fail.py').write_text('''
def up(db):
    db.execute("INVALID SQL THAT WILL FAIL")
''')
            
            manager = MigrationManager(test_db)
            monkeypatch.setattr(manager, 'migrations_dir', migrations_dir)
            
            # Try to apply - should fail
            result = manager.apply_all_pending(dry_run=False)
            assert result['success'] is False
            assert result['failed_count'] == 1
            
            # Check that the failed migration is recorded
            status = manager.get_migration_status()
            assert len(status['failed_migrations']) == 1
            assert '001' in status['failed_migrations']
            
            # Now fix the migration
            (migrations_dir / '001_fail.py').write_text('''
def up(db):
    db.execute("CREATE TABLE fixed_table (id INTEGER PRIMARY KEY)")
''')
            
            # Try again - should succeed without UNIQUE constraint errors
            # First, create a new manager instance to force re-scanning
            manager2 = MigrationManager(test_db)
            monkeypatch.setattr(manager2, 'migrations_dir', migrations_dir)
            
            result = manager2.apply_all_pending(dry_run=False)
            assert result['success'] is True
            assert result['applied_count'] == 1
            
            # Table should exist
            table_result = test_db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='fixed_table'"
            )
            assert table_result is not None and len(table_result) > 0

