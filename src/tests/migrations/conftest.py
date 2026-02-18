"""
Test fixtures for migration system tests.
"""

import tempfile
from pathlib import Path

import pytest

# The test database will be created in a temporary directory


@pytest.fixture
def temp_migrations_dir():
    """Create a temporary migrations directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        migrations_dir = Path(tmpdir) / "migrations"
        migrations_dir.mkdir(parents=True, exist_ok=True)
        yield migrations_dir


@pytest.fixture
def sample_migration(temp_migrations_dir):
    """Create a sample migration file for testing."""
    migration_file = temp_migrations_dir / "001_test_migration.py"

    content = '''
def up(db):
    """Test migration up."""
    db.execute("""
        CREATE TABLE test_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255)
        )
    """)

def down(db):
    """Test migration down."""
    db.execute("DROP TABLE IF EXISTS test_table")
'''

    migration_file.write_text(content)
    return migration_file


@pytest.fixture
def migration_with_data(temp_migrations_dir):
    """Create a migration that inserts test data."""
    migration_file = temp_migrations_dir / "002_add_data.py"

    content = '''
def up(db):
    """Add test data."""
    db.execute("""
        INSERT INTO test_table (name) VALUES (?)
    """, ("test_value",))

def down(db):
    """Remove test data."""
    db.execute("DELETE FROM test_table WHERE name = ?", ("test_value",))
'''

    migration_file.write_text(content)
    return migration_file


@pytest.fixture
def migration_without_down(temp_migrations_dir):
    """Create a migration without down() function."""
    migration_file = temp_migrations_dir / "003_no_rollback.py"

    content = '''
def up(db):
    """Migration without rollback."""
    db.execute("CREATE INDEX idx_test ON test_table(name)")
'''

    migration_file.write_text(content)
    return migration_file


@pytest.fixture
def migration_with_error(temp_migrations_dir):
    """Create a migration that will fail."""
    migration_file = temp_migrations_dir / "004_error.py"

    content = '''
def up(db):
    """This migration will fail."""
    db.execute("CREATE TABLE nonexistent_table FROM invalid_source")

def down(db):
    """Rollback."""
    db.execute("DROP TABLE error_table")
'''

    migration_file.write_text(content)
    return migration_file
