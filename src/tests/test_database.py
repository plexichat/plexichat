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

import utils.config as config
import utils.logger as logger
from src.core.database.core import Database

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
        
    default_config = {
        "database": {
            "type": "sqlite",
            "path": db_path
        }
    }
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
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
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
    db.execute("UPDATE users SET username = ? WHERE username = ?", ("alice_updated", "alice"))
    
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
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, age INTEGER)")
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
    special_data = "Test's \"quoted\" data with $special @chars!"
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
    with open(log_file, 'r') as f:
        content = f.read()
        assert "Connected to SQLite" in content

def test_logging_query_execution(db_config):
    """Test that queries are logged."""
    log_file = "temp_test/logs/latest.log"
    db = Database()
    db.connect()
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
    db.close()
    
    with open(log_file, 'r') as f:
        content = f.read()
        assert "Executed query" in content

def test_logging_errors(db_config):
    """Test that errors are logged."""
    log_file = "temp_test/logs/latest.log"
    db = Database()
    db.connect()
    try:
        db.execute("INVALID SQL")
    except:
        pass
    db.close()
    
    with open(log_file, 'r') as f:
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
    db.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER, content TEXT)")
    
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
    with open(log_file, 'r') as f:
        content = f.read()
        assert "Database initialized" in content

# PostgreSQL Tests
def test_postgres_connection_real(db_config):
    """Test PostgreSQL connection with real driver."""
    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not installed")

    pg_config = {
        "type": "postgres",
        "postgres": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "password",
            "dbname": "postgres"
        }
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
            "dbname": "postgres"
        }
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
            "dbname": "postgres"
        }
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
    db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, int_val INTEGER, real_val REAL)")
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
    db.execute("INSERT INTO users (username, email) VALUES (?, ?)", ("alice", "alice@example.com"))
    
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
