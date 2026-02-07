"""
Tests for the database migration system.
"""

import pytest
import shutil
import tempfile
from pathlib import Path
from src.core.migrations.manager import MigrationManager, Migration
from src.core.migrations.validator import calculate_checksum

@pytest.fixture
def mock_db(mocker):
    """Create a mock database instance."""
    db = mocker.Mock()
    db.type = "sqlite"
    # Mock basic fetch/execute
    db.fetch_all.return_value = []
    db.fetch_one.return_value = None
    return db

@pytest.fixture
def temp_migrations_dir():
    """Create a temporary directory for migration files."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)

def test_calculate_checksum():
    """Test checksum calculation."""
    content = b"test content"
    checksum = calculate_checksum(content)
    assert len(checksum) == 64
    assert checksum == calculate_checksum(b"test content")

def test_migration_manager_init(mock_db):
    """Test MigrationManager initialization."""
    manager = MigrationManager(mock_db)
    assert manager.db == mock_db
    assert manager.migrations_dir.exists()

def test_get_pending_migrations(mock_db, temp_migrations_dir, mocker):
    """Test discovery of pending migrations."""
    # Setup manager with temp dir
    mocker.patch('src.core.migrations.manager.Path.parent', temp_migrations_dir.parent)
    manager = MigrationManager(mock_db)
    manager.migrations_dir = temp_migrations_dir
    
    # Create mock migration files
    file1 = temp_migrations_dir / "001_initial.py"
    file1.write_text("def up(db): pass\n\ndef down(db): pass\n")
    
    file2 = temp_migrations_dir / "002_update.py"
    file2.write_text("def up(db): pass\n\ndef down(db): pass\n")
    
    # Mock tracker to return only 001 as applied
    mocker.patch.object(manager.tracker, 'get_applied_migrations', return_value=['001'])
    mocker.patch.object(manager.tracker, 'ensure_table_exists')
    
    pending = manager.get_pending_migrations()
    
    assert len(pending) == 1
    assert pending[0].version == "002"
    assert pending[0].name == "update"

def test_apply_all_pending_success(mock_db, mocker):
    """Test successful application of all pending migrations."""
    manager = MigrationManager(mock_db)
    
    # Mock pending migrations
    m1 = Migration("001", "first", "path1", "hash1")
    m2 = Migration("002", "second", "path2", "hash2")
    mocker.patch.object(manager, 'get_pending_migrations', return_value=[m1, m2])
    
    # Mock internal execution
    mock_exec = mocker.patch.object(manager, '_execute_migration')
    mock_exec.side_effect = [
        {'success': True, 'version': '001', 'execution_time_ms': 10, 'message': 'ok'},
        {'success': True, 'version': '002', 'execution_time_ms': 15, 'message': 'ok'}
    ]
    
    mocker.patch('src.core.migrations.validator.validate_migration_order', return_value=True)
    mocker.patch.object(manager.tracker, 'get_applied_migrations', return_value=[])
    
    results = manager.apply_all_pending()
    
    assert results['success'] is True
    assert results['applied_count'] == 2
    assert len(results['migrations']) == 2
    assert mock_exec.call_count == 2

def test_validate_integrity_mismatch(mock_db, temp_migrations_dir, mocker):
    """Test integrity check detecting checksum mismatches."""
    manager = MigrationManager(mock_db)
    manager.migrations_dir = temp_migrations_dir
    
    # Create migration file
    file1 = temp_migrations_dir / "001_test.py"
    file1.write_text("test")
    
    # Mock records with a DIFFERENT checksum
    mock_records = [
        {
            'version': '001',
            'status': 'completed',
            'checksum': 'wrong_hash'
        }
    ]
    mocker.patch.object(manager.tracker, 'get_all_migration_records', return_value=mock_records)
    mocker.patch('src.core.migrations.validator.get_migration_files', return_value=[('001', str(file1))])
    
    results = manager.validate_migration_integrity()
    
    assert results['valid'] is False
    assert len(results['mismatches']) == 1
    assert results['mismatches'][0]['version'] == '001'
    assert 'Checksum mismatch' in results['mismatches'][0]['error']
