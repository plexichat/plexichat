"""
Tests for migration validators.
"""

import hashlib
import pytest
from pathlib import Path

from src.core.migrations.validator import (
    calculate_checksum,
    validate_migration_file,
    validate_checksum,
    validate_migration_order,
    validate_sql_safety,
    get_migration_files,
)


class TestChecksumCalculation:
    """Test checksum calculation."""
    
    def test_calculate_checksum(self):
        """Test SHA256 checksum calculation."""
        content = b"test migration content"
        checksum = calculate_checksum(content)
        
        # Verify it's a valid hex string
        assert isinstance(checksum, str)
        assert len(checksum) == 64
        assert all(c in '0123456789abcdef' for c in checksum)
    
    def test_checksum_consistency(self):
        """Test that same content produces same checksum."""
        content = b"test content"
        checksum1 = calculate_checksum(content)
        checksum2 = calculate_checksum(content)
        
        assert checksum1 == checksum2
    
    def test_checksum_different_content(self):
        """Test that different content produces different checksums."""
        checksum1 = calculate_checksum(b"content1")
        checksum2 = calculate_checksum(b"content2")
        
        assert checksum1 != checksum2


class TestMigrationFileValidation:
    """Test migration file validation."""
    
    def test_valid_migration_file(self, sample_migration):
        """Test validation of valid migration file."""
        assert validate_migration_file(str(sample_migration)) is True
    
    def test_missing_migration_file(self):
        """Test validation of non-existent file."""
        with pytest.raises(FileNotFoundError):
            validate_migration_file('/nonexistent/migration.py')
    
    def test_migration_missing_up_function(self, temp_migrations_dir):
        """Test validation of migration without up() function."""
        bad_migration = temp_migrations_dir / 'bad_migration.py'
        bad_migration.write_text('def down(db): pass')
        
        with pytest.raises(ValueError, match='missing required.*up'):
            validate_migration_file(str(bad_migration))


class TestChecksumValidation:
    """Test checksum validation."""
    
    def test_matching_checksums(self):
        """Test validation with matching checksums."""
        # Should not raise
        validate_checksum('001', 'abc123', 'abc123')
    
    def test_mismatching_checksums(self):
        """Test validation with different checksums."""
        with pytest.raises(ValueError, match='Checksum mismatch'):
            validate_checksum('001', 'abc123', 'def456')


class TestMigrationOrderValidation:
    """Test migration order validation."""
    
    def test_valid_order(self):
        """Test validation of valid migration sequence."""
        pending = ['004', '005']
        applied = ['001', '002', '003']
        
        assert validate_migration_order(pending, applied) is True
    
    def test_gap_in_sequence(self):
        """Test validation detects gaps in version sequence."""
        pending = ['005']
        applied = ['001', '002', '004']  # Missing 003
        
        with pytest.raises(ValueError, match='Gap in migration'):
            validate_migration_order(pending, applied)
    
    def test_empty_applied(self):
        """Test validation with no applied migrations."""
        pending = ['001', '002']
        applied = []
        
        assert validate_migration_order(pending, applied) is True
    
    def test_single_migration(self):
        """Test validation with single migration."""
        pending = ['001']
        applied = []
        
        assert validate_migration_order(pending, applied) is True


class TestSQLSafetyValidation:
    """Test SQL safety validation."""
    
    def test_safe_sql(self):
        """Test safe SQL passes validation."""
        sql = "CREATE TABLE users (id INTEGER PRIMARY KEY)"
        is_safe, warnings = validate_sql_safety(sql)
        
        assert is_safe is True
        assert len(warnings) == 0
    
    def test_drop_database_detected(self):
        """Test dangerous DROP DATABASE pattern is detected."""
        sql = "DROP DATABASE mydb"
        
        with pytest.raises(ValueError, match='Dangerous SQL'):
            validate_sql_safety(sql)
    
    def test_drop_schema_detected(self):
        """Test dangerous DROP SCHEMA pattern is detected."""
        sql = "DROP SCHEMA public"
        
        with pytest.raises(ValueError, match='Dangerous SQL'):
            validate_sql_safety(sql)
    
    def test_truncate_migrations_history_detected(self):
        """Test truncating migrations_history is detected."""
        sql = "TRUNCATE migrations_history"
        
        with pytest.raises(ValueError, match='Dangerous SQL'):
            validate_sql_safety(sql)
    
    def test_truncate_warning(self):
        """Test TRUNCATE generates warning."""
        sql = "TRUNCATE other_table"
        is_safe, warnings = validate_sql_safety(sql)
        
        assert is_safe is True
        assert len(warnings) > 0
        assert any('TRUNCATE' in w for w in warnings)
    
    def test_drop_table_warning(self):
        """Test DROP TABLE generates warning."""
        sql = "DROP TABLE users"
        is_safe, warnings = validate_sql_safety(sql)
        
        assert is_safe is True
        assert len(warnings) > 0
        assert any('DROP TABLE' in w for w in warnings)
    
    def test_alter_table_warning(self):
        """Test ALTER TABLE generates warning."""
        sql = "ALTER TABLE users ADD COLUMN name VARCHAR(255)"
        is_safe, warnings = validate_sql_safety(sql)
        
        assert is_safe is True
        assert len(warnings) > 0
        assert any('ALTER TABLE' in w for w in warnings)


class TestGetMigrationFiles:
    """Test migration file discovery."""
    
    def test_get_migration_files(self, temp_migrations_dir, sample_migration):
        """Test discovering migration files."""
        # Create multiple migrations
        (temp_migrations_dir / '001_first.py').write_text('def up(db): pass')
        (temp_migrations_dir / '002_second.py').write_text('def up(db): pass')
        (temp_migrations_dir / '003_third.py').write_text('def up(db): pass')
        
        migrations = get_migration_files(str(temp_migrations_dir))
        
        assert len(migrations) == 3
        # Should be sorted by version
        assert migrations[0][0] == '001'
        assert migrations[1][0] == '002'
        assert migrations[2][0] == '003'
    
    def test_get_migration_files_excludes_init(self, temp_migrations_dir):
        """Test that __init__.py is excluded."""
        (temp_migrations_dir / '__init__.py').write_text('')
        (temp_migrations_dir / '001_test.py').write_text('def up(db): pass')
        
        migrations = get_migration_files(str(temp_migrations_dir))
        
        assert len(migrations) == 1
        assert migrations[0][0] == '001'
    
    def test_get_migration_files_nonexistent_dir(self):
        """Test error on non-existent directory."""
        with pytest.raises(NotADirectoryError):
            get_migration_files('/nonexistent/directory')
    
    def test_get_migration_files_sorted_order(self, temp_migrations_dir):
        """Test migrations are sorted by version number."""
        # Create out of order
        (temp_migrations_dir / '003_third.py').write_text('def up(db): pass')
        (temp_migrations_dir / '001_first.py').write_text('def up(db): pass')
        (temp_migrations_dir / '002_second.py').write_text('def up(db): pass')
        
        migrations = get_migration_files(str(temp_migrations_dir))
        
        versions = [v for v, _ in migrations]
        assert versions == ['001', '002', '003']
