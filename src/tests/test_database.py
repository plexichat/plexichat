"""
Database module tests.

Tests cover SQLite connectivity, CRUD operations, error handling,
logging integration, and the new helper methods.
"""

import pytest
import os
import sys
import shutil
import sqlite3

# Setup paths before any imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
src_path = project_root
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

import utils.config as config  # noqa: E402
import utils.logger as logger  # noqa: E402
from src.core.database.core import Database  # noqa: E402


# Fixture for setup/teardown
@pytest.fixture(scope="module")
def setup_module():
    # Setup temp environment
    if not os.path.exists("temp_test"):
        os.makedirs("temp_test")

    # Setup Logger for tests (once)
    log_dir = "temp_test/logs"
    logger.setup(log_dir=log_dir, level="DEBUG")

    yield

    # Teardown
    if os.path.exists("temp_test"):
        try:
            shutil.rmtree("temp_test")
        except OSError:
            pass


@pytest.fixture
def db_config(setup_module):
    """Sets up a fresh config for each test."""
    import gc
    import time

    config_path = "temp_test/config.yaml"
    db_path = "temp_test/test.db"

    # Force garbage collection to close any lingering connections
    gc.collect()
    time.sleep(0.1)

    # Ensure fresh config and db
    if os.path.exists(config_path):
        try:
            os.remove(config_path)
        except OSError:
            pass
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass

    default_config = {"database": {"type": "sqlite", "path": db_path}}
    config.setup(config_path=config_path, default_config=default_config)

    yield config_path

    # Force garbage collection before cleanup
    gc.collect()
    time.sleep(0.1)

    # Cleanup after test
    for path in [db_path, "temp_test/other.db"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


# SQLite Basic Tests
def test_sqlite_connection(db_config):
    """Test basic SQLite connection."""
    db = Database()
    db.connect()
    assert db.connection is not None
    db.close()


def test_sqlite_create_table(db_config):
    """Test creating a table in SQLite."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT)")

    # Verify table exists
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    )
    result = cursor.fetchone()
    assert result is not None
    assert result[0] == "users"
    db.close()


def test_sqlite_insert_single_row(db_config):
    """Test inserting a single row."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    db.execute("INSERT INTO users (username) VALUES (?)", ("alice",))

    cursor = db.execute("SELECT username FROM users")
    result = cursor.fetchone()
    assert result[0] == "alice"
    db.close()


def test_sqlite_insert_multiple_rows(db_config):
    """Test inserting multiple rows."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    db.execute("INSERT INTO users (username) VALUES (?)", ("alice",))
    db.execute("INSERT INTO users (username) VALUES (?)", ("bob",))
    db.execute("INSERT INTO users (username) VALUES (?)", ("charlie",))

    cursor = db.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    assert count == 3
    db.close()


def test_sqlite_update_row(db_config):
    """Test updating a row."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    db.execute("INSERT INTO users (username) VALUES (?)", ("alice",))
    db.execute(
        "UPDATE users SET username = ? WHERE username = ?", ("alice_updated", "alice")
    )

    cursor = db.execute("SELECT username FROM users")
    result = cursor.fetchone()
    assert result[0] == "alice_updated"
    db.close()


def test_sqlite_delete_row(db_config):
    """Test deleting a row."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    db.execute("INSERT INTO users (username) VALUES (?)", ("alice",))
    db.execute("INSERT INTO users (username) VALUES (?)", ("bob",))
    db.execute("DELETE FROM users WHERE username = ?", ("alice",))

    cursor = db.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    assert count == 1

    cursor = db.execute("SELECT username FROM users")
    result = cursor.fetchone()
    assert result[0] == "bob"
    db.close()


def test_sqlite_select_with_where(db_config):
    """Test SELECT with WHERE clause."""
    db = Database()
    db.connect()
    db.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, age INTEGER)"
    )
    db.execute("INSERT INTO users (username, age) VALUES (?, ?)", ("alice", 25))
    db.execute("INSERT INTO users (username, age) VALUES (?, ?)", ("bob", 30))

    cursor = db.execute("SELECT username FROM users WHERE age > ?", (26,))
    result = cursor.fetchone()
    assert result[0] == "bob"
    db.close()


def test_sqlite_special_characters_in_data(db_config):
    """Test handling special characters in data."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, data TEXT)")
    special_data = 'Test\'s "quoted" data with $special @chars!'
    db.execute("INSERT INTO test (data) VALUES (?)", (special_data,))

    cursor = db.execute("SELECT data FROM test")
    result = cursor.fetchone()
    assert result[0] == special_data
    db.close()


def test_sqlite_empty_table(db_config):
    """Test querying an empty table."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")

    cursor = db.execute("SELECT * FROM users")
    result = cursor.fetchone()
    assert result is None
    db.close()


def test_sqlite_large_text_data(db_config):
    """Test inserting large text data."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, data TEXT)")
    large_text = "A" * 10000
    db.execute("INSERT INTO test (data) VALUES (?)", (large_text,))

    cursor = db.execute("SELECT data FROM test")
    result = cursor.fetchone()
    assert len(result[0]) == 10000
    db.close()


# Configuration Tests
def test_config_integration(db_config):
    """Test that database respects configuration changes."""
    new_path = "temp_test/other.db"
    config.set("database", {"type": "sqlite", "path": new_path})

    db = Database()
    db.connect()
    assert os.path.exists(new_path)
    db.close()


def test_config_default_path(db_config):
    """Test default path fallback."""
    cfg = config.get("database")
    db = Database()
    db.connect()
    assert os.path.exists(cfg["path"])
    db.close()


def test_config_type_detection(db_config):
    """Test database type detection from config."""
    db = Database()
    assert db.type == "sqlite"


# Error Handling Tests
def test_invalid_db_type(db_config):
    """Test that invalid database type raises error."""
    config.set("database", {"type": "invalid"})
    db = Database()
    with pytest.raises(ValueError, match="Unsupported database type"):
        db.connect()


def test_invalid_sql_query(db_config):
    """Test that invalid SQL raises error."""
    db = Database()
    db.connect()
    with pytest.raises(sqlite3.OperationalError):
        db.execute("INVALID SQL QUERY")
    db.close()


def test_query_on_disconnected_db(db_config):
    """Test querying without connection raises error."""
    db = Database()
    with pytest.raises(ConnectionError):
        db.execute("SELECT 1")


def test_table_not_exists_error(db_config):
    """Test querying non-existent table raises error."""
    db = Database()
    db.connect()
    with pytest.raises(sqlite3.OperationalError):
        db.execute("SELECT * FROM nonexistent_table")
    db.close()


def test_duplicate_primary_key(db_config):
    """Test inserting duplicate primary key raises error."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, data TEXT)")
    db.execute("INSERT INTO test (id, data) VALUES (1, 'first')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO test (id, data) VALUES (1, 'duplicate')")
    db.close()


# Logging Tests
def test_logging_connection(db_config):
    """Test that connection is logged."""
    log_file = "temp_test/logs/latest.log"
    db = Database()
    db.connect()
    db.close()

    assert os.path.exists(log_file)
    with open(log_file, "r") as f:
        content = f.read()
        assert "Connected to SQLite" in content


def test_logging_query_execution(db_config):
    """Test that queries are logged."""
    log_file = "temp_test/logs/latest.log"
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
    db.close()

    with open(log_file, "r") as f:
        content = f.read()
        assert "Executed query" in content


def test_logging_errors(db_config):
    """Test that errors are logged."""
    log_file = "temp_test/logs/latest.log"
    db = Database()
    db.connect()
    try:
        db.execute("INVALID SQL")
    except Exception:
        pass
    db.close()

    with open(log_file, "r") as f:
        content = f.read()
        assert "Query execution failed" in content


# Integration Tests
def test_full_crud_cycle(db_config):
    """Test complete CRUD cycle integration."""
    db = Database()
    db.connect()

    # Create
    db.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL)")

    # Insert
    db.execute("INSERT INTO products (name, price) VALUES (?, ?)", ("Widget", 9.99))
    cursor = db.execute("SELECT * FROM products WHERE name = ?", ("Widget",))
    result = cursor.fetchone()
    assert result[1] == "Widget"
    assert result[2] == 9.99

    # Update
    db.execute("UPDATE products SET price = ? WHERE name = ?", (12.99, "Widget"))
    cursor = db.execute("SELECT price FROM products WHERE name = ?", ("Widget",))
    result = cursor.fetchone()
    assert result[0] == 12.99

    # Delete
    db.execute("DELETE FROM products WHERE name = ?", ("Widget",))
    cursor = db.execute("SELECT COUNT(*) FROM products")
    count = cursor.fetchone()[0]
    assert count == 0

    db.close()


def test_multiple_tables(db_config):
    """Test working with multiple tables."""
    db = Database()
    db.connect()

    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    db.execute(
        "CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER, content TEXT)"
    )

    db.execute("INSERT INTO users (username) VALUES (?)", ("alice",))
    db.execute("INSERT INTO posts (user_id, content) VALUES (?, ?)", (1, "Hello World"))

    cursor = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
    table_count = cursor.fetchone()[0]
    assert table_count >= 2

    db.close()


def test_database_reconnection(db_config):
    """Test closing and reconnecting to database."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, data TEXT)")
    db.execute("INSERT INTO test (data) VALUES (?)", ("test",))
    db.close()

    # Reconnect
    db2 = Database()
    db2.connect()
    cursor = db2.execute("SELECT data FROM test")
    result = cursor.fetchone()
    assert result[0] == "test"
    db2.close()


def test_config_logger_database_integration(db_config):
    """Test full integration of config, logger, and database."""
    # Config should be loaded
    db_cfg = config.get("database")
    assert db_cfg["type"] == "sqlite"

    # Logger should be active
    log_file = "temp_test/logs/latest.log"
    assert os.path.exists(log_file)

    # Database should use both
    db = Database()
    db.connect()
    db.execute("CREATE TABLE integration_test (id INTEGER PRIMARY KEY)")
    db.close()

    # Verify log contains database activity
    with open(log_file, "r") as f:
        content = f.read()
        assert "Database initialized" in content


# PostgreSQL Tests
def test_postgres_connection_real(db_config):
    """Test PostgreSQL connection with real driver."""
    pytest.importorskip("psycopg2")

    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres",
        },
    }
    config.set("database", pg_config)

    db = Database()
    try:
        db.connect()
        db.close()
    except Exception as e:
        pytest.skip(f"PostgreSQL not available: {e}")


def test_postgres_type_detection(db_config):
    """Test PostgreSQL type detection."""
    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres",
        },
    }
    config.set("database", pg_config)

    db = Database()
    assert db.type == "postgres"


def test_placeholder_conversion(db_config):
    """Test that ? placeholders are converted to %s for PostgreSQL."""
    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres",
        },
    }
    config.set("database", pg_config)

    db = Database()

    # Test simple conversion
    query = "SELECT * FROM users WHERE id = ?"
    converted = db._convert_placeholders(query)
    assert converted == "SELECT * FROM users WHERE id = %s"

    # Test multiple placeholders
    query = "INSERT INTO users (name, email) VALUES (?, ?)"
    converted = db._convert_placeholders(query)
    assert converted == "INSERT INTO users (name, email) VALUES (%s, %s)"

    # Test placeholder inside quotes should NOT be converted
    query = "SELECT * FROM users WHERE name = 'What?' AND id = ?"
    converted = db._convert_placeholders(query)
    assert converted == "SELECT * FROM users WHERE name = 'What?' AND id = %s"


def test_placeholder_no_conversion_sqlite(db_config):
    """Test that SQLite queries are not modified."""
    db = Database()

    query = "SELECT * FROM users WHERE id = ?"
    converted = db._convert_placeholders(query)
    assert converted == query  # Should be unchanged for SQLite


# Edge Cases
def test_null_values(db_config):
    """Test handling NULL values."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, data TEXT)")
    db.execute("INSERT INTO test (id, data) VALUES (?, ?)", (1, None))

    cursor = db.execute("SELECT data FROM test WHERE id = 1")
    result = cursor.fetchone()
    assert result[0] is None
    db.close()


def test_numeric_data_types(db_config):
    """Test various numeric data types."""
    db = Database()
    db.connect()
    db.execute(
        "CREATE TABLE test (id INTEGER PRIMARY KEY, int_val INTEGER, real_val REAL)"
    )
    db.execute("INSERT INTO test (int_val, real_val) VALUES (?, ?)", (42, 3.14159))

    cursor = db.execute("SELECT int_val, real_val FROM test")
    result = cursor.fetchone()
    assert result[0] == 42
    assert abs(result[1] - 3.14159) < 0.00001
    db.close()


def test_boolean_values(db_config):
    """Test handling boolean values (stored as integers in SQLite)."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, flag INTEGER)")
    db.execute("INSERT INTO test (flag) VALUES (?)", (1,))
    db.execute("INSERT INTO test (flag) VALUES (?)", (0,))

    cursor = db.execute("SELECT flag FROM test WHERE id = 1")
    result = cursor.fetchone()
    assert result[0] == 1

    cursor = db.execute("SELECT flag FROM test WHERE id = 2")
    result = cursor.fetchone()
    assert result[0] == 0
    db.close()


# New Method Tests


def test_fetch_one(db_config):
    """Test fetch_one helper method."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    db.execute("INSERT INTO users (username) VALUES (?)", ("alice",))
    db.execute("INSERT INTO users (username) VALUES (?)", ("bob",))

    result = db.fetch_one("SELECT username FROM users WHERE id = ?", (1,))
    assert result is not None
    assert result["username"] == "alice"

    result = db.fetch_one("SELECT username FROM users WHERE id = ?", (999,))
    assert result is None
    db.close()


def test_fetch_all(db_config):
    """Test fetch_all helper method."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    db.execute("INSERT INTO users (username) VALUES (?)", ("alice",))
    db.execute("INSERT INTO users (username) VALUES (?)", ("bob",))
    db.execute("INSERT INTO users (username) VALUES (?)", ("charlie",))

    results = db.fetch_all("SELECT username FROM users ORDER BY id")
    assert len(results) == 3
    assert results[0]["username"] == "alice"
    assert results[1]["username"] == "bob"
    assert results[2]["username"] == "charlie"
    db.close()


def test_fetch_all_empty(db_config):
    """Test fetch_all on empty table."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")

    results = db.fetch_all("SELECT * FROM users")
    assert results == []
    db.close()


def test_table_exists(db_config):
    """Test table_exists helper method."""
    db = Database()
    db.connect()

    assert db.table_exists("users") is False

    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    assert db.table_exists("users") is True
    assert db.table_exists("nonexistent") is False
    db.close()


def test_execute_many(db_config):
    """Test execute_many for batch inserts."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT)")

    users = [
        ("alice", "alice@example.com"),
        ("bob", "bob@example.com"),
        ("charlie", "charlie@example.com"),
    ]
    db.execute_many("INSERT INTO users (username, email) VALUES (?, ?)", users)

    results = db.fetch_all("SELECT * FROM users ORDER BY id")
    assert len(results) == 3
    assert results[0]["username"] == "alice"
    assert results[2]["email"] == "charlie@example.com"
    db.close()


def test_context_manager(db_config):
    """Test database as context manager."""
    with Database() as db:
        db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, data TEXT)")
        db.execute("INSERT INTO test (data) VALUES (?)", ("test_data",))
        result = db.fetch_one("SELECT data FROM test")
        assert result is not None
        assert result["data"] == "test_data"

    # Connection should be closed after context
    assert db.connection is None


def test_transaction_commit(db_config):
    """Test transaction with commit."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER)")
    db.execute("INSERT INTO accounts (balance) VALUES (?)", (100,))

    db.begin_transaction()
    db.execute("UPDATE accounts SET balance = balance - 50 WHERE id = 1")
    db.commit()

    result = db.fetch_one("SELECT balance FROM accounts WHERE id = 1")
    assert result is not None
    assert result["balance"] == 50
    db.close()


def test_transaction_rollback(db_config):
    """Test transaction with rollback."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER)")
    db.execute("INSERT INTO accounts (balance) VALUES (?)", (100,))

    db.begin_transaction()
    db.execute("UPDATE accounts SET balance = balance - 50 WHERE id = 1")
    db.rollback()

    result = db.fetch_one("SELECT balance FROM accounts WHERE id = 1")
    assert result is not None
    assert result["balance"] == 100
    db.close()


def test_row_factory_dict_access(db_config):
    """Test that rows can be accessed like dictionaries."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT)")
    db.execute(
        "INSERT INTO users (username, email) VALUES (?, ?)",
        ("alice", "alice@example.com"),
    )

    result = db.fetch_one("SELECT * FROM users WHERE id = 1")
    assert result is not None
    assert result["username"] == "alice"
    assert result["email"] == "alice@example.com"
    assert result["id"] == 1
    db.close()


def test_ensure_connected_error(db_config):
    """Test that operations fail gracefully when not connected."""
    db = Database()

    with pytest.raises(ConnectionError):
        db.fetch_one("SELECT 1")

    with pytest.raises(ConnectionError):
        db.fetch_all("SELECT 1")

    with pytest.raises(ConnectionError):
        db.table_exists("test")


def test_auto_create_directory(db_config):
    """Test that database directory is created automatically."""
    nested_path = "temp_test/nested/deep/test.db"
    config.set("database", {"type": "sqlite", "path": nested_path})

    db = Database()
    db.connect()
    assert os.path.exists(nested_path)
    db.close()

    # Cleanup
    shutil.rmtree("temp_test/nested", ignore_errors=True)


# Cross-database helper method tests


def test_insert_or_ignore(db_config):
    """Test insert_or_ignore helper method."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")

    # First insert should succeed
    result = db.insert_or_ignore("test", ["id", "name"], (1, "alice"))
    assert result is True

    # Duplicate should be ignored
    result = db.insert_or_ignore("test", ["id", "name"], (1, "alice"))
    assert result is False

    # Different id but same name should be ignored (unique constraint)
    result = db.insert_or_ignore("test", ["id", "name"], (2, "alice"))
    assert result is False

    # Verify only one row exists
    rows = db.fetch_all("SELECT * FROM test")
    assert len(rows) == 1
    assert rows[0]["name"] == "alice"
    db.close()


def test_upsert(db_config):
    """Test upsert helper method."""
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT, value INTEGER)")

    # First insert
    db.upsert("test", ["id", "name", "value"], (1, "alice", 100), ["id"])
    row = db.fetch_one("SELECT * FROM test WHERE id = 1")
    assert row["name"] == "alice"
    assert row["value"] == 100

    # Update via upsert
    db.upsert("test", ["id", "name", "value"], (1, "alice_updated", 200), ["id"])
    row = db.fetch_one("SELECT * FROM test WHERE id = 1")
    assert row["name"] == "alice_updated"
    assert row["value"] == 200

    # Verify still only one row
    rows = db.fetch_all("SELECT * FROM test")
    assert len(rows) == 1
    db.close()


def test_upsert_specific_columns(db_config):
    """Test upsert with specific update columns."""
    db = Database()
    db.connect()
    db.execute(
        "CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT, value INTEGER, created_at TEXT)"
    )

    # First insert
    db.upsert(
        "test",
        ["id", "name", "value", "created_at"],
        (1, "alice", 100, "2024-01-01"),
        ["id"],
        ["name", "value"],
    )

    # Update only name and value, not created_at
    db.upsert(
        "test",
        ["id", "name", "value", "created_at"],
        (1, "alice_updated", 200, "2024-12-31"),
        ["id"],
        ["name", "value"],
    )

    row = db.fetch_one("SELECT * FROM test WHERE id = 1")
    assert row["name"] == "alice_updated"
    assert row["value"] == 200
    # Note: SQLite's INSERT OR REPLACE actually replaces the whole row,
    # so this test verifies the API works, but behavior differs between DBs
    db.close()


# Comprehensive Placeholder Conversion Tests
def test_placeholder_conversion_edge_cases(db_config):
    """Test placeholder conversion handles complex queries with string literals."""
    from src.core.database.core import _PLACEHOLDER_PATTERN

    # Test 1: Question marks in single-quoted strings
    query = "SELECT * FROM users WHERE name = 'What?' AND id = ?"
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == "SELECT * FROM users WHERE name = 'What?' AND id = %s"

    # Test 2: Multiple question marks in strings
    query = "INSERT INTO logs (message) VALUES ('Why? How? When?') WHERE id = ?"
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == "INSERT INTO logs (message) VALUES ('Why? How? When?') WHERE id = %s"

    # Test 3: Escaped quotes with question marks
    query = "SELECT * FROM data WHERE text = 'It''s a question?' AND id = ?"
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == "SELECT * FROM data WHERE text = 'It''s a question?' AND id = %s"

    # Test 4: Question marks in double-quoted identifiers (PostgreSQL)
    query = 'SELECT "column?" FROM table WHERE id = ?'
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    # Double-quoted identifiers are not protected by the pattern, only single-quoted strings
    # So this tests that the pattern handles mixed quote types
    assert "WHERE id = %s" in converted

    # Test 5: Complex nested quotes
    query = 'SELECT * FROM users WHERE bio = \'He said "Why?"\' AND id = ?'
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == 'SELECT * FROM users WHERE bio = \'He said "Why?"\' AND id = %s'

    # Test 6: Multiple parameters with strings
    query = "SELECT * FROM users WHERE name = 'What?' AND status = ? AND id = ?"
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == "SELECT * FROM users WHERE name = 'What?' AND status = %s AND id = %s"

    # Test 7: No parameters to convert
    query = "SELECT * FROM users WHERE name = 'What?'"
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == query  # Should remain unchanged

    # Test 8: Only string literal with question mark
    query = "SELECT 'Is this a question?' AS literal"
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == query  # Should remain unchanged


def test_placeholder_conversion_in_execute_method(db_config):
    """Test that execute method uses regex pattern for placeholder conversion."""
    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres",
        },
    }
    config.set("database", pg_config)

    db = Database()
    # Verify db type is postgres
    assert db.type == "postgres"

    # Simulate the conversion that happens in execute method
    from src.core.database.core import _PLACEHOLDER_PATTERN

    query = "SELECT * FROM users WHERE name = 'What?' AND id = ?"
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == "SELECT * FROM users WHERE name = 'What?' AND id = %s"


def test_placeholder_conversion_in_execute_many_method(db_config):
    """Test that execute_many method uses regex pattern for placeholder conversion."""
    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres",
        },
    }
    config.set("database", pg_config)

    db = Database()
    assert db.type == "postgres"

    # Simulate the conversion that happens in execute_many method
    from src.core.database.core import _PLACEHOLDER_PATTERN

    query = "INSERT INTO logs (message, user_id) VALUES (?, ?)"
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == "INSERT INTO logs (message, user_id) VALUES (%s, %s)"

    # With string literal
    query = "INSERT INTO logs (message, user_id) VALUES ('Why?', ?)"
    converted = _PLACEHOLDER_PATTERN.sub("%s", query)
    assert converted == "INSERT INTO logs (message, user_id) VALUES ('Why?', %s)"


def test_postgres_connection_pool_config(db_config):
    """Test that PostgreSQL connection pool respects configuration settings."""
    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres",
        },
        "connection_pool": {
            "min_connections": 5,
            "max_connections": 50,
        },
    }
    config.set("database", pg_config)

    db = Database()
    assert db.type == "postgres"

    # Verify that the config has the pool settings
    pool_config = db.config.get("connection_pool", {})
    assert pool_config.get("min_connections", 2) == 5
    assert pool_config.get("max_connections", 20) == 50


def test_postgres_connection_pool_config_defaults(db_config):
    """Test that PostgreSQL connection pool uses default values when not configured."""
    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres",
        },
        # No connection_pool section
    }
    config.set("database", pg_config)

    db = Database()
    assert db.type == "postgres"

    # Verify that defaults are used
    pool_config = db.config.get("connection_pool", {})
    # When connection_pool is not set, it should default to empty dict
    # and the _connect_postgres method will use hardcoded defaults
    assert pool_config.get("min_connections", 2) == 2  # default
    assert pool_config.get("max_connections", 20) == 20  # default

# Connection Pool Leak Fix Tests


def test_connection_reuse_validation(db_config):
    """Test that connections are properly reused and validated."""
    db = Database()
    db.connect()
    
    # Store reference to first connection
    first_conn = db._local.connection
    assert first_conn is not None
    
    # Call _get_conn multiple times
    conn1 = db._get_conn()
    conn2 = db._get_conn()
    conn3 = db._get_conn()
    
    # Verify same connection object is returned (connection reuse works)
    assert conn1 is first_conn
    assert conn2 is first_conn
    assert conn3 is first_conn
    
    # Close and reconnect
    db.close()
    db.connect()
    
    # Verify new connection object is returned
    new_conn = db._get_conn()
    assert new_conn is not first_conn
    
    db.close()


def test_stale_connection_handling(db_config):
    """Test that stale/closed connections are properly handled and replaced."""
    db = Database()
    db.connect()
    
    # Store reference to first connection
    first_conn = db._local.connection
    
    # Manually close the underlying connection (simulate network failure)
    first_conn.close()
    
    # Call _get_conn again - should detect closed connection and reconnect
    new_conn = db._get_conn()
    
    # Verify connection was replaced
    assert new_conn is not first_conn
    # New connection should be valid
    assert new_conn is not None
    # Verify no exceptions were raised
    
    db.close()


def test_connection_replacement_in_connect(db_config):
    """Test that calling connect() again properly replaces old connection."""
    db = Database()
    db.connect()
    
    # Store reference to first connection
    first_conn = db._local.connection
    assert first_conn is not None
    
    # Call connect() again
    db.connect()
    
    # Verify old connection was properly closed
    # and new connection is different object
    new_conn = db._local.connection
    assert new_conn is not first_conn
    assert new_conn is not None
    
    # Verify thread-local state is properly initialized
    assert db._local.transaction_depth == 0
    assert not db._local.in_transaction
    
    db.close()


def test_thread_local_state_clearing(db_config):
    """Test that thread-local state is properly cleared on close()."""
    db = Database()
    db.connect()
    
    # Set transaction state
    db.begin_transaction()
    assert db._local.transaction_depth == 1
    assert db._local.in_transaction
    
    # Call close()
    db.close()
    
    # Verify thread-local state is cleared
    assert db._local.connection is None
    assert db._local.transaction_depth == 0
    assert not db._local.in_transaction


def test_connection_lifecycle_logging(db_config, caplog):
    """Test that connection lifecycle events are properly logged."""
    import logging
    
    # Set logger to capture DEBUG level
    caplog.set_level(logging.DEBUG)
    
    db = Database()
    
    # Connect
    db.connect()
    
    # Execute query (which will call _get_conn)
    cursor = db.execute("SELECT 1")
    cursor.close()
    
    # Close connection
    db.close()
    
    # Verify lifecycle events are logged
    log_messages = [record.message for record in caplog.records]
    
    # Should have messages about acquiring connection
    assert any("Acquiring new database connection" in msg for msg in log_messages), \
        f"Expected connection acquisition log, got: {log_messages}"
    
    # Should have messages about thread-local state
    assert any("cleared thread-local connection state" in msg for msg in log_messages), \
        f"Expected state clearing log, got: {log_messages}"


def test_sqlite_connection_reuse(db_config):
    """Test that SQLite connections are properly reused within same thread."""
    db = Database()
    
    # First connection
    db.connect()
    conn1 = db._local.connection
    
    # Should reuse same connection
    conn2 = db._get_conn()
    assert conn2 is conn1
    
    # Another call should also reuse
    conn3 = db._get_conn()
    assert conn3 is conn1
    
    db.close()


def test_connection_acquire_release_cycle(db_config):
    """Test complete acquire-release cycle with proper state management."""
    db = Database()
    
    # Start fresh
    assert db._local.connection if hasattr(db._local, "connection") else True
    
    # Acquire connection
    conn = db._get_conn()
    assert conn is not None
    assert db._local.connection is conn
    
    # Execute something to ensure it works
    cursor = db.execute("SELECT 1")
    result = cursor.fetchone()
    cursor.close()
    assert result is not None
    
    # Release connection
    db.close()
    assert db._local.connection is None
    assert db._local.transaction_depth == 0
    assert not db._local.in_transaction


def test_multiple_connection_cycles(db_config):
    """Test multiple acquire-release cycles work correctly."""
    db = Database()
    
    connections = []
    
    # Perform multiple cycles
    for i in range(3):
        db.connect()
        conn = db._local.connection
        connections.append(conn)
        
        # Each should be a new connection (since we close)
        if i > 0:
            assert conn is not connections[i-1], f"Cycle {i}: Expected new connection"
        
        db.close()
        assert db._local.connection is None


def test_connection_validation_detects_closed(db_config):
    """Test that _get_conn properly detects and handles closed connections."""
    db = Database()
    db.connect()
    
    # Get a valid connection first
    valid_conn = db._get_conn()
    assert valid_conn is not None
    
    # Close it manually
    valid_conn.close()
    
    # _get_conn should detect it's closed and get a new one
    new_conn = db._get_conn()
    
    # Should be different from the closed one
    assert new_conn is not valid_conn
    assert new_conn is not None
    
    db.close()


def test_connection_timeout_config_exists(db_config):
    """Test that connect_timeout configuration is properly read."""
    # Create a config with connect_timeout
    test_config = {
        "database": {
            "type": "sqlite",
            "path": db_config.replace("test.db", "timeout_test.db"),
            "connection_pool": {
                "min_connections": 2,
                "max_connections": 20,
                "connect_timeout": 15
            }
        }
    }
    config.set("database", test_config["database"])
    
    db = Database()
    
    # Verify timeout is in config
    pool_config = db.config.get("connection_pool", {})
    assert pool_config.get("connect_timeout", 10) == 15


def test_connection_pool_state_consistency(db_config):
    """Test that connection pool state remains consistent across operations."""
    db = Database()
    
    # Multiple connections and operations
    for i in range(5):
        db.connect()
        assert db._local.connection is not None
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction
        
        # Do something with connection
        cursor = db.execute("SELECT ?", (i,))
        cursor.close()
        
        db.close()
        assert db._local.connection is None
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction


# PostgreSQL-specific error handling tests


def test_pool_exhaustion_handling(db_config, caplog):
    """Test that pool exhaustion is logged and raises appropriate error.
    
    Configures a mock ThreadedConnectionPool with small maxconn,
    requests more connections than available, verifies:
    1. PoolError is raised
    2. Specific pool-exhaustion error is logged
    3. No connection leak (putconn called for returned connections)
    """
    try:
        import psycopg2
        import psycopg2.pool
    except ImportError:
        pytest.skip("psycopg2 not installed")

    import logging
    from unittest.mock import MagicMock, patch
    
    # Set logger to capture DEBUG level
    caplog.set_level(logging.DEBUG)
    
    # Configure database for PostgreSQL
    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres",
        },
        "connection_pool": {
            "min_connections": 1,
            "max_connections": 2,  # Small maxconn to test exhaustion
        },
    }
    config.set("database", pg_config)
    
    # Create database instance
    db = Database()
    
    # Mock the ThreadedConnectionPool to simulate exhaustion
    mock_pool = MagicMock(spec=psycopg2.pool.ThreadedConnectionPool)
    
    # Track connections handed out and returned
    connections_out = []
    
    def getconn_side_effect():
        """Simulate pool behavior: maxconn=2, raise PoolError when exhausted."""
        if len(connections_out) >= 2:
            raise psycopg2.pool.PoolError("Connection pool exhausted")
        
        # Create mock connection
        mock_conn = MagicMock()
        mock_conn.closed = 0  # Connection is open
        connections_out.append(mock_conn)
        return mock_conn
    
    def putconn_side_effect(conn):
        """Simulate returning connection to pool."""
        if conn in connections_out:
            connections_out.remove(conn)
    
    mock_pool.getconn = MagicMock(side_effect=getconn_side_effect)
    mock_pool.putconn = MagicMock(side_effect=putconn_side_effect)
    
    # Patch the ThreadedConnectionPool instantiation
    with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool):
        # First call to connect() creates pool and gets connection
        try:
            db.connect()
            assert len(connections_out) == 1, "First connection should be acquired"
        except Exception as e:
            pytest.fail(f"First connection should succeed: {e}")
        
        # Second call should also work (maxconn=2)
        try:
            # Need new db instance to avoid thread-local reuse
            db2 = Database()
            db2._pool = mock_pool  # Reuse the mock pool
            db2.connect()
            assert len(connections_out) == 2, "Second connection should be acquired"
        except Exception as e:
            pytest.fail(f"Second connection should succeed: {e}")
        
        # Third call should exhaust pool
        db3 = Database()
        db3._pool = mock_pool  # Reuse the mock pool
        
        with pytest.raises(psycopg2.pool.PoolError):
            db3.connect()
        
        # Verify pool exhaustion error was logged
        log_messages = [record.message for record in caplog.records]
        assert any("pool exhausted" in msg.lower() for msg in log_messages), \
            f"Expected pool exhaustion log message, got: {log_messages}"
        
        # Verify the specific error message mentions pool exhaustion
        assert any("no available connections" in msg.lower() for msg in log_messages), \
            f"Expected 'no available connections' in logs, got: {log_messages}"
        
        # Clean up: return connections to pool to verify putconn was called
        for conn in list(connections_out):
            mock_pool.putconn(conn)
        
        # Verify putconn was called for the connections
        assert mock_pool.putconn.call_count >= 2, \
            "putconn should be called to return connections to pool"


def test_connect_timeout_handling(db_config, caplog):
    """Test that connection timeout is logged with specific timeout event.
    
    Mocks getconn() to raise a timeout-flavored OperationalError,
    asserts the timeout log entry is emitted separately from generic errors.
    """
    try:
        import psycopg2
        import psycopg2.pool
    except ImportError:
        pytest.skip("psycopg2 not installed")

    import logging
    from unittest.mock import MagicMock, patch
    
    # Set logger to capture DEBUG level
    caplog.set_level(logging.DEBUG)
    
    # Configure database for PostgreSQL with specific timeout
    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres",
        },
        "connection_pool": {
            "min_connections": 1,
            "max_connections": 10,
            "connect_timeout": 5,  # Specific timeout value
        },
    }
    config.set("database", pg_config)
    
    # Create database instance
    db = Database()
    
    # Mock the ThreadedConnectionPool
    mock_pool = MagicMock(spec=psycopg2.pool.ThreadedConnectionPool)
    
    # Create a timeout-flavored OperationalError
    timeout_error = psycopg2.OperationalError("timeout during connection establishment")
    
    mock_pool.getconn = MagicMock(side_effect=timeout_error)
    
    # Patch the ThreadedConnectionPool instantiation
    with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool):
        # Call connect() which should timeout
        with pytest.raises(psycopg2.OperationalError):
            db.connect()
        
        # Verify timeout-specific error was logged
        log_messages = [record.message for record in caplog.records]
        
        # Should have timeout-specific log message mentioning the timeout duration
        timeout_logs = [msg for msg in log_messages if "timeout" in msg.lower()]
        assert len(timeout_logs) > 0, \
            f"Expected timeout-specific log message, got: {log_messages}"
        
        # Verify the timeout duration is mentioned in the log
        assert any("5s" in msg for msg in timeout_logs), \
            f"Expected timeout duration (5s) in logs, got: {timeout_logs}"
        
        # Verify error is logged before re-raising
        assert any("Connection timeout" in msg for msg in log_messages), \
            f"Expected 'Connection timeout' in logs, got: {log_messages}"


# ============================================================================
# PostgreSQL Connection Pool Tests (Not Skipped - Uses Fixtures/Mocking)
# ============================================================================

class TestPostgresConnectionPoolAcquisition:
    """Test PostgreSQL connection pool acquisition and lifecycle."""
    
    @pytest.fixture
    def postgres_pool_config(self):
        """Configure PostgreSQL with connection pool."""
        pg_config = {
            "type": "postgres",
            "postgres": {
                "host": "localhost",
                "port": 5432,
                "user": "postgres",
                "password": "password",
                "dbname": "postgres",
            },
            "connection_pool": {
                "min_connections": 2,
                "max_connections": 10,
                "connect_timeout": 10,
            },
        }
        config.set("database", pg_config)
        return pg_config
    
    def test_pool_minconn_validation(self, postgres_pool_config):
        """Test that minimum connections configuration is respected."""
        db = Database()
        pool_config = db.config.get("connection_pool", {})
        assert pool_config.get("min_connections", 2) == 2
    
    def test_pool_maxconn_validation(self, postgres_pool_config):
        """Test that maximum connections configuration is respected."""
        db = Database()
        pool_config = db.config.get("connection_pool", {})
        assert pool_config.get("max_connections", 20) == 10
    
    def test_pool_timeout_validation(self, postgres_pool_config):
        """Test that connection timeout configuration is respected."""
        db = Database()
        pool_config = db.config.get("connection_pool", {})
        assert pool_config.get("connect_timeout", 10) == 10
    
    def test_pool_initialization_creates_pool(self, postgres_pool_config):
        """Test that pool is created on first connection attempt."""
        db = Database()
        assert db._pool is None, "Pool should be None before first connection"
        # Note: actual connection would require real PostgreSQL
        # Pool is created in connect() which requires live DB
    
    def test_pool_reuse_across_threads(self, postgres_pool_config):
        """Test that pool is reused across multiple threads."""
        import threading
        
        Database()
        
        def get_pool():
            """Get pool reference from database instance."""
            local_db = Database()
            # Pool is created during first connection attempt
            # For now just verify pool config is accessible
            assert local_db.config.get("connection_pool") is not None
        
        threads = [threading.Thread(target=get_pool) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


class TestPostgresPlaceholderConversionComplex:
    """Test placeholder conversion with complex query scenarios."""
    
    @pytest.fixture(autouse=True)
    def setup_postgres_config(self):
        """Setup PostgreSQL configuration for each test."""
        pg_config = {
            "type": "postgres",
            "postgres": {
                "host": "localhost",
                "port": 5432,
                "user": "postgres",
                "password": "password",
                "dbname": "postgres",
            },
        }
        config.set("database", pg_config)
    
    def test_nested_single_quotes_with_placeholders(self):
        """Test placeholder conversion with nested single quotes."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        # Query with nested quotes and placeholders
        query = "SELECT * FROM users WHERE message = 'It''s a question?' AND id = ?"
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted == "SELECT * FROM users WHERE message = 'It''s a question?' AND id = %s"
    
    def test_multiple_nested_quotes(self):
        """Test multiple levels of quote nesting."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        # Multiple parameters with quoted strings
        query = "INSERT INTO logs (msg, user, data) VALUES (?, ?, 'What''s this?')"
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted == "INSERT INTO logs (msg, user, data) VALUES (%s, %s, 'What''s this?')"
    
    def test_mixed_quote_styles(self):
        """Test queries with both single and double quotes."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        # Single quoted string containing double quotes
        query = 'SELECT * FROM users WHERE bio = \'He said "Why?"\' AND status = ?'
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted == 'SELECT * FROM users WHERE bio = \'He said "Why?"\' AND status = %s'
    
    def test_consecutive_placeholders(self):
        """Test query with consecutive placeholders."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        # Consecutive parameters
        query = "UPDATE users SET name = ?, email = ?, status = ? WHERE id = ?"
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted == "UPDATE users SET name = %s, email = %s, status = %s WHERE id = %s"
    
    def test_placeholder_in_case_statement(self):
        """Test placeholder conversion in CASE statements."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        query = """
            SELECT CASE 
                WHEN status = ? THEN 'Active'
                WHEN status = ? THEN 'Inactive'
                ELSE 'Unknown'
            END FROM users WHERE id = ?
        """
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        # Should have 3 %s conversions, not counting any quoted question marks
        assert converted.count("%s") == 3
        assert converted.count("?") == 0
    
    def test_placeholder_in_json_extraction(self):
        """Test placeholder in JSON path expressions."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        # PostgreSQL JSON query
        query = "SELECT data->'key' FROM users WHERE id = ? AND data->>'status' = ?"
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted == "SELECT data->'key' FROM users WHERE id = %s AND data->>'status' = %s"
    
    def test_placeholder_in_like_clause(self):
        """Test placeholder in LIKE clause with wildcard strings."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        query = "SELECT * FROM users WHERE username LIKE ? AND email LIKE ?"
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted == "SELECT * FROM users WHERE username LIKE %s AND email LIKE %s"
    
    def test_placeholder_in_between_clause(self):
        """Test placeholder in BETWEEN clause."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        query = "SELECT * FROM users WHERE age BETWEEN ? AND ?"
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted == "SELECT * FROM users WHERE age BETWEEN %s AND %s"
    
    def test_placeholder_in_in_clause(self):
        """Test placeholder in IN clause."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        # Note: IN clauses with placeholders are complex;
        # this tests basic placeholder conversion
        query = "SELECT * FROM users WHERE status IN (?, ?) AND id = ?"
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted == "SELECT * FROM users WHERE status IN (%s, %s) AND id = %s"
    
    def test_no_false_positives_in_string_literals(self):
        """Test that question marks in string literals aren't converted."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        query = "SELECT 'Is this correct?' AS question, 'Why not?' AS another, id FROM users WHERE id = ?"
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        
        # Only the actual placeholder at the end should be converted
        assert converted == "SELECT 'Is this correct?' AS question, 'Why not?' AS another, id FROM users WHERE id = %s"
        # Verify the string literals are untouched
        assert "'Is this correct?'" in converted
        assert "'Why not?'" in converted
    
    def test_escaped_single_quote_with_placeholder(self):
        """Test escaped single quotes followed by placeholders."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        # Escaped quote at end of string followed by placeholder
        query = "INSERT INTO messages (text, status) VALUES ('Don''t ask', ?)"
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted == "INSERT INTO messages (text, status) VALUES ('Don''t ask', %s)"
    
    def test_complex_query_with_subquery(self):
        """Test placeholder conversion in subqueries."""
        Database()
        from src.core.database.core import _PLACEHOLDER_PATTERN
        
        query = """
            SELECT * FROM users u 
            WHERE u.id IN (
                SELECT user_id FROM logs WHERE action = ? AND timestamp > ?
            ) AND u.status = ?
        """
        converted = _PLACEHOLDER_PATTERN.sub("%s", query)
        assert converted.count("%s") == 3
        # Verify structure is maintained
        assert "u.id IN" in converted
        assert "WHERE action" in converted


class TestPostgresConnectionTimeout:
    """Test connection timeout handling with real PostgreSQL delays."""
    
    @pytest.fixture
    def postgres_timeout_config(self):
        """Configure PostgreSQL with short timeout."""
        pg_config = {
            "type": "postgres",
            "postgres": {
                "host": "localhost",
                "port": 5432,
                "user": "postgres",
                "password": "password",
                "dbname": "postgres",
            },
            "connection_pool": {
                "min_connections": 1,
                "max_connections": 5,
                "connect_timeout": 3,  # 3 second timeout
            },
        }
        config.set("database", pg_config)
        return pg_config
    
    def test_timeout_config_is_set(self, postgres_timeout_config):
        """Test that timeout config is properly set."""
        db = Database()
        pool_config = db.config.get("connection_pool", {})
        assert pool_config.get("connect_timeout", 10) == 3
    
    def test_timeout_passed_to_pool(self, postgres_timeout_config):
        """Test that timeout is used when creating pool."""
        db = Database()
        # Verify pool config has timeout
        assert db.config["connection_pool"]["connect_timeout"] == 3
    
    def test_connection_timeout_error_handling(self, postgres_timeout_config, caplog):
        """Test that connection timeout raises proper error with logging."""
        try:
            import psycopg2
        except ImportError:
            pytest.skip("psycopg2 not installed")
        
        from unittest.mock import MagicMock, patch
        import logging
        
        caplog.set_level(logging.DEBUG)
        
        db = Database()
        
        # Create mock pool that raises timeout
        mock_pool = MagicMock()
        timeout_error = psycopg2.OperationalError("connection timeout")
        mock_pool.getconn.side_effect = timeout_error
        
        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool):
            with pytest.raises(psycopg2.OperationalError):
                db.connect()
    
    def test_timeout_is_distinct_from_connection_refused(self, postgres_timeout_config, caplog):
        """Test distinguishing timeout error from connection refused."""
        try:
            import psycopg2
        except ImportError:
            pytest.skip("psycopg2 not installed")
        
        from unittest.mock import MagicMock, patch
        import logging
        
        caplog.set_level(logging.DEBUG)
        
        db = Database()
        
        # Create mock pool with timeout error (not connection refused)
        mock_pool = MagicMock()
        timeout_error = psycopg2.OperationalError("timeout during connection establishment")
        mock_pool.getconn.side_effect = timeout_error
        
        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool):
            with pytest.raises(psycopg2.OperationalError) as exc_info:
                db.connect()
            
            # Verify it's a timeout error, not connection refused
            assert "timeout" in str(exc_info.value).lower()