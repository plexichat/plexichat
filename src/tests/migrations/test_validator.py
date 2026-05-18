"""Tests for migration validation utilities."""

import pytest
import tempfile
import os

from src.core.migrations.validator import (
    validate_migration_order,
    get_migration_files,
    calculate_checksum,
    validate_migration_file,
    validate_checksum,
    validate_sql_safety,
)


@pytest.mark.migrations
class TestValidator:
    """Tests for migration validation utilities."""

    def test_validate_migration_order_empty(self):
        """Test validating empty pending list."""
        assert validate_migration_order([], []) is True

    def test_validate_migration_order_sequential(self):
        """Test validating sequential pending migrations."""
        assert validate_migration_order(["001", "002", "003"], []) is True

    def test_validate_migration_order_gap_raises(self):
        """Test that gap in pending versions raises ValueError."""
        with pytest.raises(ValueError, match="Gap"):
            validate_migration_order(["001", "003"], [])

    def test_validate_migration_order_with_applied(self):
        """Test validating with already-applied migrations."""
        assert validate_migration_order(["003", "004"], ["001", "002"]) is True

    def test_validate_migration_order_gap_in_applied_warns(self):
        """Test that gaps in applied migrations are logged but not blocked."""
        # Should not raise, just warn
        assert validate_migration_order(["005"], ["001", "003"]) is True

    def test_get_migration_files_not_dir(self):
        """Test that non-existent directory raises NotADirectoryError."""
        with pytest.raises(NotADirectoryError):
            get_migration_files("/nonexistent/dir")

    def test_get_migration_files_valid_dir(self, migration_manager):
        """Test getting migration files from valid directory."""
        files = get_migration_files(str(migration_manager.migrations_dir))
        assert isinstance(files, list)
        # Should have at least the initial schema
        assert len(files) > 0

    def test_calculate_checksum_consistency(self):
        """Test that checksum is consistent for same content."""
        data = b"test content"
        c1 = calculate_checksum(data)
        c2 = calculate_checksum(data)
        assert c1 == c2

    def test_calculate_checksum_different_data(self):
        """Test that different data produces different checksums."""
        c1 = calculate_checksum(b"data1")
        c2 = calculate_checksum(b"data2")
        assert c1 != c2

    def test_calculate_checksum_string_input(self):
        """Test that string input is handled."""
        c = calculate_checksum("test")
        assert len(c) == 64  # SHA-256 hex digest length

    def test_calculate_checksum_length(self):
        """Test that checksum has correct length."""
        c = calculate_checksum(b"test")
        assert len(c) == 64

    def test_validate_migration_file_missing(self):
        """Test validating a missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            validate_migration_file("/nonexistent/migration.py")

    def test_validate_migration_file_no_up(self):
        """Test validating file without up() raises ValueError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("# No up or down\ndef something(): pass\n")
            f.flush()
            with pytest.raises(ValueError, match="missing required up"):
                validate_migration_file(f.name)
        os.unlink(f.name)

    def test_validate_migration_file_no_down(self):
        """Test validating file without down() raises ValueError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def up(db): pass\n")
            f.flush()
            with pytest.raises(ValueError, match="missing required down"):
                validate_migration_file(f.name)
        os.unlink(f.name)

    def test_validate_migration_file_valid(self):
        """Test validating a valid migration file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def up(db): pass\ndef down(db): pass\n")
            f.flush()
            assert validate_migration_file(f.name) is True
        os.unlink(f.name)

    def test_validate_checksum_match(self):
        """Test checksum validation with matching checksums."""
        data = b"test content"
        expected = calculate_checksum(data)
        assert validate_checksum("test_file", expected, expected) is True

    def test_validate_checksum_mismatch(self):
        """Test checksum validation with mismatched checksums."""
        with pytest.raises(ValueError, match="Checksum mismatch"):
            validate_checksum("test_file", "abc123", "def456")

    def test_validate_sql_safety_safe(self):
        """Test SQL safety validation with safe SQL."""
        is_safe, warnings = validate_sql_safety("SELECT * FROM users")
        assert is_safe is True
        assert len(warnings) == 0

    def test_validate_sql_safety_dangerous(self):
        """Test SQL safety validation with dangerous SQL."""
        with pytest.raises(ValueError, match="Dangerous SQL"):
            validate_sql_safety("DROP DATABASE mydb")

    def test_validate_sql_safety_truncate_history(self):
        """Test SQL safety validation with truncate of history table."""
        with pytest.raises(ValueError, match="Dangerous SQL"):
            validate_sql_safety("TRUNCATE migrations_history")

    def test_validate_sql_safety_warning_drop_table(self):
        """Test SQL safety validation warns on DROP TABLE."""
        is_safe, warnings = validate_sql_safety("DROP TABLE old_table")
        assert is_safe is True
        assert any("DROP TABLE" in w for w in warnings)

    def test_validate_sql_safety_warning_alter_table(self):
        """Test SQL safety validation warns on ALTER TABLE."""
        is_safe, warnings = validate_sql_safety("ALTER TABLE users ADD COLUMN x INT")
        assert is_safe is True
        assert any("ALTER TABLE" in w for w in warnings)

    def test_validate_sql_safety_warning_truncate(self):
        """Test SQL safety validation warns on TRUNCATE."""
        is_safe, warnings = validate_sql_safety("TRUNCATE some_table")
        assert is_safe is True
        assert any("TRUNCATE" in w for w in warnings)
