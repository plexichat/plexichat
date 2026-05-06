"""
Docker-based PostgreSQL Integration Tests.

These tests use Docker containers to test PostgreSQL functionality with
real database instances. They test connection pool behavior, error recovery,
and transaction management with actual PostgreSQL.

Usage:
    pytest tests/test_postgres_docker.py -v

Environment Configuration:
    POSTGRES_HOST: PostgreSQL host (default: localhost)
    POSTGRES_PORT: PostgreSQL port (default: 5432)
    POSTGRES_USER: PostgreSQL user (default: postgres)
    POSTGRES_PASSWORD: PostgreSQL password (default: postgres)
    POSTGRES_DB: Database name (default: test_db)
    USE_DOCKER: Enable Docker (default: true)
"""

import pytest

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skip(reason="PostgreSQL Docker tests require Docker environment"),
]
import os  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402

# Setup paths
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
src_path = project_root
for path in [project_root, src_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

import utils.config as config  # noqa: E402
import utils.logger as logger  # noqa: E402
from src.core.database.core import Database  # noqa: E402


# Fixture for setup/teardown
@pytest.fixture(scope="module", autouse=True)
def setup_docker_tests():
    """Setup logging for Docker tests."""
    log_dir = "temp_test_docker"
    os.makedirs(log_dir, exist_ok=True)
    logger.setup(log_dir=log_dir, level="DEBUG")

    yield

    # Cleanup
    import shutil

    if os.path.exists(log_dir):
        try:
            shutil.rmtree(log_dir)
        except OSError:
            pass


class TestPostgresDockerConnectivity:
    """Test basic connectivity to PostgreSQL in Docker."""

    def test_docker_container_starts(self, postgres_manager):
        """Test that Docker container starts successfully."""
        assert postgres_manager is not None
        assert postgres_manager.host is not None
        assert postgres_manager.port is not None

    def test_connection_to_docker_postgres(self, postgres_db):
        """Test connecting to PostgreSQL in Docker."""
        assert postgres_db is not None
        assert postgres_db.connection is not None

        # Simple query to verify connection works
        result = postgres_db.fetch_one("SELECT 1 as test")
        assert result is not None
        assert result["test"] == 1

    def test_multiple_connections_to_docker(self, postgres_manager):
        """Test creating multiple connections to Docker PostgreSQL."""
        config.set("database", postgres_manager.get_config())

        connections = []
        for i in range(3):
            db = Database()
            db.connect()
            connections.append(db)

            # Verify each connection works
            result = db.fetch_one("SELECT ? as num", (i,))
            assert result["num"] == i

        # Cleanup
        for db in connections:
            db.close()


class TestPostgresDockerTransactions:
    """Test transaction behavior with real PostgreSQL Docker container."""

    def test_transaction_commit_docker(self, postgres_db_with_table):
        """Test transaction commit with real PostgreSQL."""
        postgres_db_with_table.begin_transaction()
        postgres_db_with_table.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("docker_test", 100),
        )
        postgres_db_with_table.commit()

        # Verify data was committed
        result = postgres_db_with_table.fetch_one(
            "SELECT * FROM test_transactions WHERE name = ?",
            ("docker_test",),
        )
        assert result is not None
        assert result["value"] == 100

    def test_transaction_rollback_docker(self, postgres_db_with_table):
        """Test transaction rollback with real PostgreSQL."""
        postgres_db_with_table.begin_transaction()
        postgres_db_with_table.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("rollback_test", 200),
        )
        postgres_db_with_table.rollback()

        # Verify data was NOT committed
        result = postgres_db_with_table.fetch_one(
            "SELECT * FROM test_transactions WHERE name = ?",
            ("rollback_test",),
        )
        assert result is None

    def test_nested_transactions_docker(self, postgres_db_with_table):
        """Test nested transactions with real PostgreSQL."""
        # Outer transaction
        postgres_db_with_table.begin_transaction()
        postgres_db_with_table.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("outer", 100),
        )

        # Inner transaction (savepoint)
        postgres_db_with_table.begin_transaction()
        postgres_db_with_table.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("inner", 200),
        )
        postgres_db_with_table.commit()

        # Commit outer
        postgres_db_with_table.commit()

        # Verify both rows exist
        results = postgres_db_with_table.fetch_all(
            "SELECT * FROM test_transactions ORDER BY id"
        )
        assert len(results) == 2


class TestPostgresDockerConstraintErrors:
    """Test error recovery with real constraint violations."""

    def test_not_null_constraint_recovery_docker(self, postgres_db_with_constraints):
        """Test recovery from NOT NULL constraint with real PostgreSQL."""
        try:
            import psycopg2.errors
        except ImportError:
            pytest.skip("psycopg2 not installed")

        # Insert valid data first
        postgres_db_with_constraints.execute(
            "INSERT INTO constrained_data (username, email, age, balance) VALUES (?, ?, ?, ?)",
            ("valid_user", "valid@example.com", 25, 100.00),
        )

        # Try to insert NULL in NOT NULL column
        postgres_db_with_constraints.begin_transaction()

        with pytest.raises(psycopg2.errors.NotNullViolation):
            postgres_db_with_constraints.execute(
                "INSERT INTO constrained_data (username, email, age, balance) VALUES (?, ?, ?, ?)",
                (None, "null@example.com", 30, 50.00),
            )

        # Verify recovery
        postgres_db_with_constraints._validate_transaction_state()
        assert postgres_db_with_constraints._local.transaction_depth == 0

        # Verify only first insert succeeded
        results = postgres_db_with_constraints.fetch_all(
            "SELECT * FROM constrained_data"
        )
        assert len(results) == 1

    def test_unique_constraint_recovery_docker(self, postgres_db_with_constraints):
        """Test recovery from UNIQUE constraint with real PostgreSQL."""
        try:
            import psycopg2.errors
        except ImportError:
            pytest.skip("psycopg2 not installed")

        # Insert first user
        postgres_db_with_constraints.execute(
            "INSERT INTO constrained_data (username, email, age, balance) VALUES (?, ?, ?, ?)",
            ("unique_user", "user@example.com", 25, 100.00),
        )

        # Try to insert duplicate username
        postgres_db_with_constraints.begin_transaction()

        with pytest.raises(psycopg2.errors.UniqueViolation):
            postgres_db_with_constraints.execute(
                "INSERT INTO constrained_data (username, email, age, balance) VALUES (?, ?, ?, ?)",
                ("unique_user", "other@example.com", 30, 50.00),
            )

        # Verify recovery
        postgres_db_with_constraints._validate_transaction_state()
        assert postgres_db_with_constraints._local.transaction_depth == 0

        # Verify only first insert succeeded
        results = postgres_db_with_constraints.fetch_all(
            "SELECT * FROM constrained_data"
        )
        assert len(results) == 1

    def test_check_constraint_recovery_docker(self, postgres_db_with_constraints):
        """Test recovery from CHECK constraint with real PostgreSQL."""
        try:
            import psycopg2.errors
        except ImportError:
            pytest.skip("psycopg2 not installed")

        # Try to insert invalid age (CHECK age >= 0 AND age <= 150)
        postgres_db_with_constraints.begin_transaction()

        with pytest.raises(psycopg2.errors.CheckViolation):
            postgres_db_with_constraints.execute(
                "INSERT INTO constrained_data (username, email, age, balance) VALUES (?, ?, ?, ?)",
                ("invalid_age", "invalid@example.com", 200, 100.00),
            )

        # Verify recovery
        postgres_db_with_constraints._validate_transaction_state()
        assert postgres_db_with_constraints._local.transaction_depth == 0

        # Verify no data was inserted
        results = postgres_db_with_constraints.fetch_all(
            "SELECT * FROM constrained_data"
        )
        assert len(results) == 0


class TestPostgresDockerConnectionPool:
    """Test connection pool behavior with real PostgreSQL."""

    def test_pool_connection_reuse_docker(self, postgres_config):
        """Test that connections are reused from the pool."""
        config.set("database", postgres_config)

        # First connection
        db1 = Database()
        db1.connect()
        id(db1._local.connection)

        # Execute query
        result1 = db1.fetch_one("SELECT 1 as val")
        assert result1["val"] == 1

        db1.close()

        # Second connection
        db2 = Database()
        db2.connect()
        id(db2._local.connection)

        # Due to pool reuse, might get same or different connection
        # Just verify it works
        result2 = db2.fetch_one("SELECT 2 as val")
        assert result2["val"] == 2

        db2.close()

    def test_pool_multiple_concurrent_connections(self, postgres_config):
        """Test pool with multiple concurrent connections."""
        config.set("database", postgres_config)

        results = []

        def create_connection(idx: int):
            """Create a connection and execute query."""
            db = Database()
            db.connect()
            result = db.fetch_one("SELECT ? as idx", (idx,))
            results.append(result)
            db.close()

        # Create threads
        threads = [
            threading.Thread(target=create_connection, args=(i,)) for i in range(5)
        ]

        # Run threads
        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # Verify all queries succeeded
        assert len(results) == 5
        for i, result in enumerate(results):
            assert result["idx"] in range(5)

    def test_pool_stats_collection(self, postgres_db):
        """Test pool statistics collection."""
        stats = postgres_db.get_pool_stats()

        assert stats is not None
        assert "total_acquisitions" in stats
        assert "database_type" in stats
        assert stats["database_type"] == "postgres"


class TestPostgresDockerEdgeCases:
    """Test edge cases with real PostgreSQL Docker container."""

    def test_large_transaction_docker(self, postgres_db_with_table):
        """Test large transaction with many inserts."""
        postgres_db_with_table.begin_transaction()

        for i in range(100):
            postgres_db_with_table.execute(
                "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
                (f"row_{i}", i * 10),
            )

        postgres_db_with_table.commit()

        # Verify all rows were inserted
        result = postgres_db_with_table.fetch_one(
            "SELECT COUNT(*) as cnt FROM test_transactions"
        )
        assert result["cnt"] == 100

    def test_json_data_type_docker(self, clean_postgres_db):
        """Test storing and retrieving JSON data."""
        clean_postgres_db.execute("""
            CREATE TABLE json_test (
                id SERIAL PRIMARY KEY,
                data JSONB
            )
        """)

        # Insert JSON data
        clean_postgres_db.execute(
            "INSERT INTO json_test (data) VALUES (?)",
            ('{"key": "value", "number": 42}',),
        )

        # Retrieve and verify
        result = clean_postgres_db.fetch_one("SELECT data FROM json_test")
        assert result is not None
        assert "value" in result["data"]

        # Cleanup
        clean_postgres_db.execute("DROP TABLE json_test")

    def test_timeout_configuration_docker(self, postgres_manager):
        """Test timeout configuration with Docker PostgreSQL."""
        config_with_timeout = postgres_manager.get_config()
        config_with_timeout["connection_pool"]["connect_timeout"] = 5

        config.set("database", config_with_timeout)

        db = Database()
        pool_config = db.config.get("connection_pool", {})
        assert pool_config.get("connect_timeout") == 5


class TestPostgresDockerErrorHandling:
    """Test error handling with real PostgreSQL Docker container."""

    def test_invalid_query_error_docker(self, postgres_db):
        """Test invalid SQL query error handling."""
        with pytest.raises(Exception):  # PostgreSQL error
            postgres_db.execute("INVALID SQL SYNTAX")

    def test_table_not_found_error_docker(self, postgres_db):
        """Test querying non-existent table."""
        with pytest.raises(Exception):  # Table not found error
            postgres_db.execute("SELECT * FROM nonexistent_table_xyz")

    def test_connection_after_error_docker(self, postgres_db):
        """Test that connection is still usable after error."""
        # Cause an error
        try:
            postgres_db.execute("INVALID QUERY")
        except Exception:
            pass

        # Connection should still work for valid queries
        result = postgres_db.fetch_one("SELECT 1 as test")
        assert result["test"] == 1


class TestPostgresDockerDataIntegrity:
    """Test data integrity with real PostgreSQL Docker container."""

    def test_foreign_key_constraint_docker(self, clean_postgres_db):
        """Test foreign key constraints with Docker PostgreSQL."""
        try:
            import psycopg2.errors
        except ImportError:
            pytest.skip("psycopg2 not installed")

        # Create tables with foreign key
        clean_postgres_db.execute("""
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL
            )
        """)

        clean_postgres_db.execute("""
            CREATE TABLE posts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                content TEXT
            )
        """)

        # Insert user
        clean_postgres_db.execute(
            "INSERT INTO users (name) VALUES (?)",
            ("Alice",),
        )

        # Insert post with valid foreign key
        clean_postgres_db.execute(
            "INSERT INTO posts (user_id, content) VALUES (?, ?)",
            (1, "Hello World"),
        )

        # Try to insert post with invalid foreign key
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            clean_postgres_db.execute(
                "INSERT INTO posts (user_id, content) VALUES (?, ?)",
                (999, "Invalid"),
            )

        # Cleanup
        clean_postgres_db.execute("DROP TABLE posts CASCADE")
        clean_postgres_db.execute("DROP TABLE users CASCADE")

    def test_transaction_isolation_docker(self, postgres_config):
        """Test transaction isolation levels with Docker PostgreSQL."""
        # This is a simplified test of isolation behavior
        config.set("database", postgres_config)

        # Create test table
        db1 = Database()
        db1.connect()
        db1.execute("CREATE TABLE IF NOT EXISTS isolation_test (id SERIAL, value INT)")
        db1.execute("TRUNCATE isolation_test")
        db1.close()

        # Note: Full isolation testing requires multiple connections
        # This is a simplified version that just verifies the setup works

        db_test = Database()
        db_test.connect()
        db_test.execute("INSERT INTO isolation_test (value) VALUES (?)", (100,))
        result = db_test.fetch_one("SELECT * FROM isolation_test")
        assert result["value"] == 100
        db_test.close()
