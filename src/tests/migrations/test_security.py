"""Tests for migration security and safety checks."""

import pytest

from src.core.migrations.validator import validate_sql_safety


@pytest.mark.migrations
class TestSecurity:
    """Tests for migration security validation."""

    def test_dangerous_drop_database(self):
        """Test that DROP DATABASE is rejected."""
        with pytest.raises(ValueError, match="Dangerous SQL"):
            validate_sql_safety("DROP DATABASE production")

    def test_dangerous_drop_schema(self):
        """Test that DROP SCHEMA is rejected."""
        with pytest.raises(ValueError, match="Dangerous SQL"):
            validate_sql_safety("DROP SCHEMA public")

    def test_dangerous_truncate_history(self):
        """Test that TRUNCATE migrations_history is rejected."""
        with pytest.raises(ValueError, match="Dangerous SQL"):
            validate_sql_safety("TRUNCATE TABLE migrations_history")

    def test_warning_drop_table(self):
        """Test that DROP TABLE generates a warning."""
        is_safe, warnings = validate_sql_safety("DROP TABLE temp_data")
        assert is_safe is True
        assert len(warnings) > 0

    def test_warning_alter_table(self):
        """Test that ALTER TABLE generates a warning."""
        is_safe, warnings = validate_sql_safety(
            "ALTER TABLE users ADD COLUMN phone TEXT"
        )
        assert is_safe is True
        assert any("ALTER TABLE" in w for w in warnings)

    def test_safe_create_table(self):
        """Test that CREATE TABLE is safe with no warnings."""
        is_safe, warnings = validate_sql_safety(
            "CREATE TABLE new_table (id INTEGER PRIMARY KEY)"
        )
        assert is_safe is True
        assert len(warnings) == 0

    def test_safe_insert(self):
        """Test that INSERT is safe."""
        is_safe, warnings = validate_sql_safety(
            "INSERT INTO config (key, value) VALUES ('a', 'b')"
        )
        assert is_safe is True
        assert len(warnings) == 0

    def test_safe_select(self):
        """Test that SELECT is safe."""
        is_safe, warnings = validate_sql_safety("SELECT * FROM users WHERE id = 1")
        assert is_safe is True
        assert len(warnings) == 0

    def test_case_insensitive_dangerous(self):
        """Test that dangerous patterns are caught case-insensitively."""
        with pytest.raises(ValueError):
            validate_sql_safety("drop database mydb")

    def test_case_insensitive_drop_table_warning(self):
        """Test that warnings work case-insensitively."""
        is_safe, warnings = validate_sql_safety("drop table old_data")
        assert is_safe is True
        assert len(warnings) > 0

    def test_empty_sql(self):
        """Test that empty SQL is safe."""
        is_safe, warnings = validate_sql_safety("")
        assert is_safe is True
        assert len(warnings) == 0

    def test_whitespace_sql(self):
        """Test that whitespace-only SQL is safe."""
        is_safe, warnings = validate_sql_safety("   ")
        assert is_safe is True
        assert len(warnings) == 0
