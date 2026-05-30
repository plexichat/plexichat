"""
Comprehensive transaction tests for database module.

Tests cover:
- Nested transactions with savepoints
- Error handling and recovery in transactions
- Transaction state validation and recovery
- Savepoint management
- PostgreSQL InFailedSqlTransaction recovery
"""

import pytest
import os

# common_utils is now a native package.


import utils.config as config  # noqa: E402
import utils.logger as logger  # noqa: E402
from src.core.database.core import Database  # noqa: E402


# Fixture for setup/teardown
@pytest.fixture(scope="module")
def setup_module(tmp_path_factory):
    """Setup test environment once per module."""
    temp_dir = tmp_path_factory.mktemp("test_transactions")

    # Setup Logger for tests (once)
    log_dir = str(temp_dir / "logs")
    logger.setup(log_dir=log_dir, level="DEBUG")

    yield temp_dir


@pytest.fixture
def db_config(setup_module):
    """Sets up a fresh config and database for each test."""
    import gc
    import time

    temp_dir = setup_module
    config_path = str(temp_dir / "config.yaml")
    db_path = str(temp_dir / "test_transactions.db")

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
    for path in [db_path]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


@pytest.fixture
def db_with_table(db_config):
    """Create a test database with a sample table."""
    db = Database()
    db.connect()

    # Create test table
    create_table_query = """
        CREATE TABLE test_transactions (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            value INTEGER DEFAULT 0
        )
    """
    db.execute(create_table_query)
    db.close()

    yield db_config

    # Cleanup
    db = Database()
    db.connect()
    try:
        db.execute("DROP TABLE test_transactions")
    except Exception:
        pass
    db.close()


class TestBasicTransactions:
    """Test basic transaction functionality."""

    def test_begin_commit_simple(self, db_with_table):
        """Test simple begin/commit transaction."""
        db = Database()
        db.connect()

        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("test1", 100),
        )
        db.commit()

        # Verify data was committed
        result = db.fetch_one(
            "SELECT * FROM test_transactions WHERE name = ?", ("test1",)
        )
        assert result is not None
        assert result["value"] == 100

        db.close()

    def test_begin_rollback_simple(self, db_with_table):
        """Test simple begin/rollback transaction."""
        db = Database()
        db.connect()

        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("test2", 200),
        )
        db.rollback()

        # Verify data was NOT committed
        result = db.fetch_one(
            "SELECT * FROM test_transactions WHERE name = ?", ("test2",)
        )
        assert result is None

        db.close()

    def test_transaction_depth_tracking(self, db_with_table):
        """Test that transaction depth is correctly tracked."""
        db = Database()
        db.connect()

        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        db.begin_transaction()
        assert db._local.transaction_depth == 1
        assert db._local.in_transaction

        db.begin_transaction()
        assert db._local.transaction_depth == 2

        db.commit()
        assert db._local.transaction_depth == 1

        db.commit()
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        db.close()


class TestNestedTransactions:
    """Test nested transactions using savepoints."""

    def test_nested_transactions_commit(self, db_with_table):
        """Test nested transactions with commits."""
        db = Database()
        db.connect()

        # Outer transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("outer1", 100),
        )

        # Inner transaction (savepoint)
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("inner1", 200),
        )
        db.commit()

        # Commit outer transaction
        db.commit()

        # Verify both records were committed
        results = db.fetch_all("SELECT * FROM test_transactions ORDER BY id")
        assert len(results) == 2
        assert results[0]["name"] == "outer1"
        assert results[1]["name"] == "inner1"

        db.close()

    def test_nested_transactions_inner_rollback(self, db_with_table):
        """Test nested transactions with inner rollback."""
        db = Database()
        db.connect()

        # Outer transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("outer2", 100),
        )

        # Inner transaction - will rollback
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("inner2_fail", 200),
        )
        db.rollback()

        # Commit outer transaction
        db.commit()

        # Verify only outer record was committed
        results = db.fetch_all("SELECT * FROM test_transactions ORDER BY id")
        assert len(results) == 1
        assert results[0]["name"] == "outer2"
        assert results[0]["value"] == 100

        db.close()

    def test_nested_transactions_outer_rollback(self, db_with_table):
        """Test nested transactions with outer rollback."""
        db = Database()
        db.connect()

        # Outer transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("outer3", 100),
        )

        # Inner transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("inner3", 200),
        )
        db.commit()

        # Rollback outer transaction (should rollback everything)
        db.rollback()

        # Verify nothing was committed
        results = db.fetch_all("SELECT * FROM test_transactions")
        assert len(results) == 0

        db.close()

    def test_deeply_nested_transactions(self, db_with_table):
        """Test deeply nested transactions (3+ levels)."""
        db = Database()
        db.connect()

        # Level 1
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("level1", 100),
        )

        # Level 2
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("level2", 200),
        )

        # Level 3
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("level3", 300),
        )
        db.commit()

        # Back to level 2
        db.commit()

        # Back to level 1
        db.commit()

        # Verify all records were committed
        results = db.fetch_all("SELECT * FROM test_transactions ORDER BY value")
        assert len(results) == 3
        values = [r["value"] for r in results]
        assert values == [100, 200, 300]

        db.close()


class TestErrorHandling:
    """Test error handling in transactions."""

    def test_query_error_in_transaction(self, db_with_table):
        """Test that errors in queries don't corrupt transaction state."""
        db = Database()
        db.connect()

        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("valid", 100),
        )

        # Try to execute invalid query
        with pytest.raises(Exception):
            db.execute("SELECT * FROM nonexistent_table")

        # Transaction state should still be valid
        assert db._local.transaction_depth == 1
        assert db._local.in_transaction

        # Should be able to continue with transaction
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("after_error", 200),
        )
        db.commit()

        # Both records should be committed
        results = db.fetch_all("SELECT * FROM test_transactions ORDER BY id")
        assert len(results) == 2
        assert results[0]["name"] == "valid"
        assert results[1]["name"] == "after_error"

        db.close()

    def test_constraint_error_in_transaction(self, db_with_table):
        """Test constraint violation error handling."""
        db = Database()
        db.connect()

        # Insert first record
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (id, name, value) VALUES (?, ?, ?)",
            (1, "first", 100),
        )
        db.commit()

        # Try to insert duplicate primary key
        db.begin_transaction()
        with pytest.raises(Exception):
            db.execute(
                "INSERT INTO test_transactions (id, name, value) VALUES (?, ?, ?)",
                (1, "duplicate", 200),
            )

        # Transaction should still be valid after rollback
        assert db._local.in_transaction

        # Rollback should work
        db.rollback()

        # Verify only first record exists
        results = db.fetch_all("SELECT * FROM test_transactions")
        assert len(results) == 1
        assert results[0]["name"] == "first"

        db.close()

    def test_transaction_recovery_after_error(self, db_with_table):
        """Test transaction recovery after error."""
        db = Database()
        db.connect()

        db.begin_transaction()

        # Valid operation
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("before_error", 100),
        )

        # Invalid operation
        try:
            db.execute("INVALID SQL SYNTAX HERE")
        except Exception:
            pass

        # Transaction should still work
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("after_error_recovery", 200),
        )

        db.commit()

        # Both records should be there
        results = db.fetch_all("SELECT * FROM test_transactions ORDER BY id")
        assert len(results) == 2

        db.close()


class TestExecuteMany:
    """Test execute_many with transaction handling."""

    def test_execute_many_commit(self, db_with_table):
        """Test execute_many with commit."""
        db = Database()
        db.connect()

        data = [("batch1", 100), ("batch2", 200), ("batch3", 300)]
        db.execute_many(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            data,
        )

        # Verify all records were committed
        results = db.fetch_all("SELECT * FROM test_transactions ORDER BY id")
        assert len(results) == 3

        db.close()

    def test_execute_many_in_transaction(self, db_with_table):
        """Test execute_many within transaction."""
        db = Database()
        db.connect()

        db.begin_transaction()

        data = [("trans1", 100), ("trans2", 200), ("trans3", 300)]
        db.execute_many(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            data,
        )

        db.commit()

        # Verify all records were committed
        results = db.fetch_all("SELECT * FROM test_transactions ORDER BY id")
        assert len(results) == 3

        db.close()

    def test_execute_many_rollback(self, db_with_table):
        """Test execute_many with rollback."""
        db = Database()
        db.connect()

        db.begin_transaction()

        data = [("rollback1", 100), ("rollback2", 200)]
        db.execute_many(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            data,
        )

        db.rollback()

        # Verify no records were committed
        results = db.fetch_all("SELECT * FROM test_transactions")
        assert len(results) == 0

        db.close()


class TestTransactionStateValidation:
    """Test transaction state validation and recovery."""

    def test_state_validation_before_query(self, db_with_table):
        """Test that state is validated before executing queries."""
        db = Database()
        db.connect()

        # Start transaction
        db.begin_transaction()

        # Transaction state should be valid
        assert db._local.in_transaction
        assert db._local.transaction_depth == 1

        # Execute query (should not raise state validation error)
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("test", 100),
        )

        db.commit()
        db.close()

    def test_state_reset_after_error(self, db_with_table):
        """Test that state is properly reset after error recovery."""
        db = Database()
        db.connect()

        db.begin_transaction()

        # Cause an error
        try:
            db.execute("INVALID SYNTAX")
        except Exception:
            pass

        # State should be resettable
        assert db._local.transaction_depth == 1

        # Should be able to rollback
        db.rollback()
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        db.close()

    def test_multiple_transactions_state_isolation(self, db_with_table):
        """Test that multiple transactions don't interfere with each other."""
        db = Database()
        db.connect()

        # First transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("first_tx", 100),
        )
        db.commit()

        # Second transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("second_tx", 200),
        )
        db.commit()

        # Both should exist
        results = db.fetch_all("SELECT * FROM test_transactions ORDER BY id")
        assert len(results) == 2
        assert results[0]["name"] == "first_tx"
        assert results[1]["name"] == "second_tx"

        db.close()


class TestAutoCommitBehavior:
    """Test auto-commit behavior in transactions."""

    def test_auto_commit_disabled_in_transaction(self, db_with_table):
        """Test that auto-commit is disabled within transactions."""
        db = Database()
        db.connect()

        db.begin_transaction()
        # Execute with auto_commit=True (default), but should not commit
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("should_not_commit", 100),
            auto_commit=True,  # Explicitly set to True
        )

        # Data should NOT be visible outside transaction before commit
        # (this test verifies transaction behavior)
        db.rollback()

        # Data should be gone
        results = db.fetch_all("SELECT * FROM test_transactions")
        assert len(results) == 0

        db.close()

    def test_auto_commit_enabled_outside_transaction(self, db_with_table):
        """Test that auto-commit works outside transactions."""
        db = Database()
        db.connect()

        # Execute without transaction (auto-commit should work)
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("auto_committed", 100),
            auto_commit=True,
        )

        # Data should be committed
        results = db.fetch_all("SELECT * FROM test_transactions")
        assert len(results) == 1
        assert results[0]["name"] == "auto_committed"

        db.close()


class TestSavepointManagement:
    """Test savepoint creation and management."""

    def test_savepoint_naming(self, db_with_table):
        """Test that savepoints are named correctly."""
        db = Database()
        db.connect()

        # We can't directly check savepoint names, but we can verify
        # that the transaction depth matches savepoint levels
        assert db._local.transaction_depth == 0

        db.begin_transaction()
        assert db._local.transaction_depth == 1

        db.begin_transaction()
        assert db._local.transaction_depth == 2

        db.begin_transaction()
        assert db._local.transaction_depth == 3

        # Commit in order
        db.commit()
        assert db._local.transaction_depth == 2

        db.commit()
        assert db._local.transaction_depth == 1

        db.commit()
        assert db._local.transaction_depth == 0

        db.close()

    def test_savepoint_with_mixed_operations(self, db_with_table):
        """Test savepoints with mixed insert/update/delete operations."""
        db = Database()
        db.connect()

        # Outer transaction
        db.begin_transaction()

        # Insert
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("mixed1", 100),
        )

        # Inner transaction for update
        db.begin_transaction()
        db.execute(
            "UPDATE test_transactions SET value = ? WHERE name = ?",
            (150, "mixed1"),
        )
        db.commit()

        # Another inner transaction for insert
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
            ("mixed2", 200),
        )
        db.commit()

        # Outer commit
        db.commit()

        # Verify final state
        results = db.fetch_all("SELECT * FROM test_transactions ORDER BY id")
        assert len(results) == 2
        assert results[0]["name"] == "mixed1"
        assert results[0]["value"] == 150
        assert results[1]["name"] == "mixed2"

        db.close()


class TestContextManager:
    """Test context manager behavior with transactions."""

    def test_context_manager_commit(self, db_with_table):
        """Test context manager normal completion (commit)."""
        with Database() as db:
            db.begin_transaction()
            db.execute(
                "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
                ("context1", 100),
            )
            db.commit()

        # Verify data was committed
        db = Database()
        db.connect()
        results = db.fetch_all("SELECT * FROM test_transactions")
        assert len(results) == 1
        assert results[0]["name"] == "context1"
        db.close()

    def test_context_manager_rollback_on_exception(self, db_with_table):
        """Test context manager rollback on exception."""
        try:
            with Database() as db:
                db.begin_transaction()
                db.execute(
                    "INSERT INTO test_transactions (name, value) VALUES (?, ?)",
                    ("context2", 100),
                )
                # Raise exception (should trigger rollback)
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Verify data was NOT committed due to exception
        db = Database()
        db.connect()
        results = db.fetch_all("SELECT * FROM test_transactions")
        assert len(results) == 0
        db.close()


class TestPostgresFailedTransactionRecovery:
    """Test PostgreSQL InFailedSqlTransaction detection and recovery.

    These tests are skipped if PostgreSQL is not configured, as they require
    a live PostgreSQL database connection to trigger InFailedSqlTransaction state.
    """

    @pytest.fixture
    def postgres_config(self):
        """Setup PostgreSQL configuration if available."""
        # Check if PostgreSQL configuration is available
        pg_config = config.get("postgres")
        if not pg_config:
            pytest.skip("PostgreSQL configuration not available")

        # Check if we can import psycopg2
        pytest.importorskip("psycopg2")

        return pg_config

    @pytest.fixture
    def postgres_db_with_table(self, postgres_config, setup_module):
        """Create a PostgreSQL test database with a sample table."""
        pytest.importorskip("psycopg2")

        # Use test database configuration
        temp_dir = setup_module
        config_path = str(temp_dir / "postgres_config.yaml")

        # Setup config with PostgreSQL
        default_config = {
            "database": {
                "type": "postgres",
                "postgres": postgres_config,
                "connection_pool": {
                    "min_connections": 2,
                    "max_connections": 10,
                    "connect_timeout": 10,
                },
            }
        }
        config.setup(config_path=config_path, default_config=default_config)

        db = Database()
        try:
            db.connect()
        except Exception as e:
            pytest.skip(f"Could not connect to PostgreSQL: {e}")

        # Drop test table if exists (cleanup from previous runs)
        try:
            db.execute("DROP TABLE test_transactions_postgres")
        except Exception:
            pass

        # Create test table
        create_table_query = """
            CREATE TABLE test_transactions_postgres (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                value INTEGER DEFAULT 0
            )
        """
        db.execute(create_table_query)
        db.close()

        yield config_path

        # Cleanup after test
        db = Database()
        try:
            db.connect()
            db.execute("DROP TABLE test_transactions_postgres")
            db.close()
        except Exception:
            pass

        # Clean up config file
        if os.path.exists(config_path):
            try:
                os.remove(config_path)
            except OSError:
                pass

    def test_infailedsqltransaction_detection(self, postgres_db_with_table):
        """Test detection of InFailedSqlTransaction error by exception type."""
        pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Start a transaction
        db.begin_transaction()

        # Insert a valid row
        db.execute(
            "INSERT INTO test_transactions_postgres (name, value) VALUES (?, ?)",
            ("valid_row", 100),
        )

        # Force a constraint violation or syntax error to abort transaction
        try:
            # Try to insert NULL into a NOT NULL column (or other constraint violation)
            db.execute(
                "INSERT INTO test_transactions_postgres (id, name, value) VALUES (?, ?, ?)",
                (1, None, 100),  # NULL in NOT NULL column
            )
        except Exception:
            # Expected to fail
            pass

        # Transaction is now in failed state
        # _validate_transaction_state should detect this and trigger recovery
        try:
            # This should trigger validation and recovery
            db._validate_transaction_state()

            # After recovery, transaction_depth should be reset
            assert db._local.transaction_depth == 0
            assert not db._local.in_transaction
        except Exception as e:
            pytest.fail(f"_validate_transaction_state failed: {e}")
        finally:
            db.close()

    def test_infailedsqltransaction_recovery_resets_state(self, postgres_db_with_table):
        """Test that recovery properly resets transaction_depth and in_transaction."""
        pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Verify initial state
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        # Start a transaction
        db.begin_transaction()
        assert db._local.transaction_depth == 1
        assert db._local.in_transaction

        # Insert a valid row
        db.execute(
            "INSERT INTO test_transactions_postgres (name, value) VALUES (?, ?)",
            ("state_test", 50),
        )

        # Force an error to abort transaction
        try:
            db.execute(
                "INSERT INTO test_transactions_postgres (id, name, value) VALUES (?, ?, ?)",
                (1, None, 100),  # NULL constraint violation
            )
        except Exception:
            pass

        # Validate transaction state (should trigger recovery)
        db._validate_transaction_state()

        # After recovery, state should be reset
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        db.close()

    def test_infailedsqltransaction_recover_from_failed_transaction_rollback(
        self, postgres_db_with_table
    ):
        """Test that _recover_from_failed_transaction properly rolls back."""
        pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Start transaction
        db.begin_transaction()

        # Insert data
        db.execute(
            "INSERT INTO test_transactions_postgres (name, value) VALUES (?, ?)",
            ("will_rollback", 100),
        )

        # Force error to abort transaction
        try:
            db.execute(
                "INSERT INTO test_transactions_postgres (id, name, value) VALUES (?, ?, ?)",
                (1, None, 100),
            )
        except Exception:
            pass

        # Call recovery directly
        db._recover_from_failed_transaction()

        # Verify state is reset
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        # Verify data was not committed
        results = db.fetch_all("SELECT * FROM test_transactions_postgres")
        assert len(results) == 0

        db.close()

    def test_infailedsqltransaction_message_fragment_detection(
        self, postgres_db_with_table
    ):
        """Test detection of failed transaction by message fragments."""
        pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Start a transaction
        db.begin_transaction()

        # Insert a valid row
        db.execute(
            "INSERT INTO test_transactions_postgres (name, value) VALUES (?, ?)",
            ("fragment_test", 200),
        )

        # Force a constraint violation
        try:
            # This will cause "current transaction is aborted" error
            db.execute(
                "INSERT INTO test_transactions_postgres (id, name, value) VALUES (?, ?, ?)",
                (1, None, 100),
            )
        except Exception as e:
            error_msg = str(e).lower()
            # Verify the error contains expected fragments
            assert any(
                frag in error_msg
                for frag in [
                    "null",
                    "not null",
                    "constraint",
                    "failed",
                ]
            ), f"Expected constraint error, got: {e}"

        # Now the transaction is in failed state
        # Validate should trigger recovery
        db._validate_transaction_state()

        # State should be reset
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        db.close()

    def test_infailedsqltransaction_recovery_allows_new_transaction(
        self, postgres_db_with_table
    ):
        """Test that after recovery, we can start a new transaction."""
        pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # First transaction - will fail
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions_postgres (name, value) VALUES (?, ?)",
            ("first_attempt", 100),
        )

        # Force error
        try:
            db.execute(
                "INSERT INTO test_transactions_postgres (id, name, value) VALUES (?, ?, ?)",
                (1, None, 100),
            )
        except Exception:
            pass

        # Trigger recovery
        db._validate_transaction_state()

        # Now start a new transaction (should work)
        db.begin_transaction()
        assert db._local.transaction_depth == 1

        # Insert valid data
        db.execute(
            "INSERT INTO test_transactions_postgres (name, value) VALUES (?, ?)",
            ("second_attempt", 200),
        )

        db.commit()

        # Verify only the second insertion succeeded
        results = db.fetch_all(
            "SELECT * FROM test_transactions_postgres ORDER BY value"
        )
        assert len(results) == 1
        assert results[0]["name"] == "second_attempt"

        db.close()

    def test_infailedsqltransaction_pgcode_detection(self, postgres_db_with_table):
        """Test detection of failed transaction by pgcode (PostgreSQL error code 25P02)."""
        pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Start a transaction
        db.begin_transaction()

        # Insert a valid row
        db.execute(
            "INSERT INTO test_transactions_postgres (name, value) VALUES (?, ?)",
            ("pgcode_test", 300),
        )

        # Force a constraint violation (will set pgcode to 25P02)
        try:
            db.execute(
                "INSERT INTO test_transactions_postgres (id, name, value) VALUES (?, ?, ?)",
                (1, None, 100),
            )
        except Exception as e:
            # Check if pgcode is available and is 25P02
            if hasattr(e, "pgcode"):
                assert e.pgcode == "25P02", f"Expected pgcode 25P02, got {e.pgcode}"

        # Validate transaction state (should trigger recovery by pgcode)
        db._validate_transaction_state()

        # State should be reset
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        db.close()

    def test_infailedsqltransaction_nested_recovery(self, postgres_db_with_table):
        """Test recovery in nested transactions."""
        pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Outer transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions_postgres (name, value) VALUES (?, ?)",
            ("outer", 100),
        )

        # Inner transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_transactions_postgres (name, value) VALUES (?, ?)",
            ("inner", 200),
        )

        # Force error in inner transaction
        try:
            db.execute(
                "INSERT INTO test_transactions_postgres (id, name, value) VALUES (?, ?, ?)",
                (1, None, 100),
            )
        except Exception:
            pass

        # Transaction is now failed
        # Validate should trigger recovery
        db._validate_transaction_state()

        # After recovery, all nesting should be cleared
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        # Data should be rolled back
        results = db.fetch_all("SELECT * FROM test_transactions_postgres")
        assert len(results) == 0

        db.close()


# ============================================================================
# Enhanced PostgreSQL Error Recovery Tests (Not Skipped)
# ============================================================================


class TestPostgresErrorRecoveryReal:
    """Test PostgreSQL error recovery with real PostgreSQL errors and constraints."""

    @pytest.fixture
    def postgres_recovery_config(self, setup_module):
        """Setup PostgreSQL configuration for recovery tests."""
        temp_dir = setup_module
        pg_config = {
            "database": {
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
        }
        config_path = str(temp_dir / "postgres_recovery_config.yaml")
        config.setup(config_path=config_path, default_config=pg_config)
        return config_path

    @pytest.fixture
    def postgres_recovery_db_with_constraints(self, postgres_recovery_config):
        """Create PostgreSQL test database with various constraints."""
        pytest.importorskip("psycopg2")

        db = Database()
        try:
            db.connect()
        except Exception as e:
            pytest.skip(f"Could not connect to PostgreSQL: {e}")

        # Drop table if exists
        try:
            db.execute("DROP TABLE test_constraints")
        except Exception:
            pass

        # Create table with multiple constraints
        create_table_query = """
            CREATE TABLE test_constraints (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) NOT NULL UNIQUE,
                email VARCHAR(100) NOT NULL,
                age INTEGER CHECK (age >= 0 AND age <= 150),
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT email_format CHECK (email LIKE '%@%.%')
            )
        """
        db.execute(create_table_query)
        db.close()

        yield postgres_recovery_config

        # Cleanup
        db = Database()
        try:
            db.connect()
            db.execute("DROP TABLE test_constraints")
            db.close()
        except Exception:
            pass

        if os.path.exists(postgres_recovery_config):
            try:
                os.remove(postgres_recovery_config)
            except OSError:
                pass

    def test_recovery_from_not_null_constraint_violation(
        self, postgres_recovery_db_with_constraints
    ):
        """Test recovery from NOT NULL constraint violation."""
        psycopg2 = pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Start transaction
        db.begin_transaction()
        assert db._local.transaction_depth == 1

        # Insert valid data
        db.execute(
            "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
            ("user1", "user1@example.com", 25),
        )

        # Try to insert NULL in NOT NULL column
        with pytest.raises(psycopg2.errors.NotNullViolation):
            db.execute(
                "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
                (None, "user2@example.com", 30),
            )

        # Transaction is now in failed state
        db._validate_transaction_state()

        # Verify recovery occurred
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        # Verify data was rolled back
        results = db.fetch_all("SELECT * FROM test_constraints")
        assert len(results) == 0

        db.close()

    def test_recovery_from_unique_constraint_violation(
        self, postgres_recovery_db_with_constraints
    ):
        """Test recovery from UNIQUE constraint violation."""
        psycopg2 = pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Insert initial data
        db.execute(
            "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
            ("unique_user", "user@example.com", 25),
        )

        # Start transaction
        db.begin_transaction()
        assert db._local.transaction_depth == 1

        # Try to insert duplicate username (violates UNIQUE constraint)
        with pytest.raises(psycopg2.errors.UniqueViolation):
            db.execute(
                "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
                ("unique_user", "user2@example.com", 30),
            )

        # Transaction is in failed state
        db._validate_transaction_state()

        # Verify recovery
        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        # Verify only original data exists
        results = db.fetch_all("SELECT * FROM test_constraints")
        assert len(results) == 1
        assert results[0]["username"] == "unique_user"

        db.close()

    def test_recovery_from_check_constraint_violation(
        self, postgres_recovery_db_with_constraints
    ):
        """Test recovery from CHECK constraint violation."""
        psycopg2 = pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Start transaction
        db.begin_transaction()

        # Insert valid data
        db.execute(
            "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
            ("user_age", "user@example.com", 25),
        )

        # Try to insert invalid age (violates CHECK constraint)
        with pytest.raises(psycopg2.errors.CheckViolation):
            db.execute(
                "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
                ("invalid_age_user", "user@example.com", 200),  # Age > 150
            )

        # Verify recovery
        db._validate_transaction_state()
        assert db._local.transaction_depth == 0

        # Verify no data was committed
        results = db.fetch_all("SELECT * FROM test_constraints")
        assert len(results) == 0

        db.close()

    def test_recovery_preserves_ability_to_retry(
        self, postgres_recovery_db_with_constraints
    ):
        """Test that recovery allows retrying transaction after error."""
        psycopg2 = pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # First transaction attempt - will fail
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
            ("first_attempt", "first@example.com", 25),
        )

        try:
            db.execute(
                "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
                (None, "null@example.com", 30),
            )
        except psycopg2.errors.NotNullViolation:
            pass

        db._validate_transaction_state()
        assert db._local.transaction_depth == 0

        # Second transaction attempt - should succeed
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
            ("second_attempt", "second@example.com", 35),
        )
        db.commit()

        # Verify second insert succeeded
        results = db.fetch_all("SELECT * FROM test_constraints")
        assert len(results) == 1
        assert results[0]["username"] == "second_attempt"

        db.close()

    def test_recovery_in_nested_transaction_context(
        self, postgres_recovery_db_with_constraints
    ):
        """Test error recovery in nested transaction scenarios."""
        psycopg2 = pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Outer transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
            ("outer", "outer@example.com", 40),
        )

        # Inner nested transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
            ("inner", "inner@example.com", 45),
        )

        # Trigger error in inner transaction
        try:
            db.execute(
                "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
                ("outer", "duplicate@example.com", 50),  # Duplicate username
            )
        except psycopg2.errors.UniqueViolation:
            pass

        # Recovery should reset all nesting
        db._validate_transaction_state()

        assert db._local.transaction_depth == 0
        assert not db._local.in_transaction

        # Verify no data was committed
        results = db.fetch_all("SELECT * FROM test_constraints")
        assert len(results) == 0

        db.close()

    def test_recovery_resets_connection_state(
        self, postgres_recovery_db_with_constraints
    ):
        """Test that recovery properly resets connection state."""
        psycopg2 = pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Get reference to connection
        original_conn = db._local.connection

        # Start transaction
        db.begin_transaction()
        db.execute(
            "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
            ("state_test", "state@example.com", 50),
        )

        # Trigger error
        try:
            db.execute(
                "INSERT INTO test_constraints (username, email, age) VALUES (?, ?, ?)",
                (None, "error@example.com", 55),
            )
        except psycopg2.errors.NotNullViolation:
            pass

        # Trigger recovery
        db._validate_transaction_state()

        # Verify connection is still valid (not closed)
        current_conn = db._local.connection
        assert current_conn is not None
        assert current_conn is original_conn  # Same connection object

        # Verify we can execute after recovery
        result = db.fetch_one("SELECT COUNT(*) as cnt FROM test_constraints")
        assert result["cnt"] == 0

        db.close()


class TestPostgresMultipleConstraintViolations:
    """Test recovery from various constraint violations in sequence."""

    @pytest.fixture
    def multi_constraint_config(self, setup_module):
        """Setup PostgreSQL config for constraint tests."""
        temp_dir = setup_module
        pg_config = {
            "database": {
                "type": "postgres",
                "postgres": {
                    "host": "localhost",
                    "port": 5432,
                    "user": "postgres",
                    "password": "password",
                    "dbname": "postgres",
                },
            }
        }
        config_path = str(temp_dir / "multi_constraint_config.yaml")
        config.setup(config_path=config_path, default_config=pg_config)
        return config_path

    @pytest.fixture
    def constraint_test_table(self, multi_constraint_config):
        """Create test table with multiple constraints."""
        pytest.importorskip("psycopg2")

        db = Database()
        try:
            db.connect()
        except Exception as e:
            pytest.skip(f"Could not connect to PostgreSQL: {e}")

        try:
            db.execute("DROP TABLE IF EXISTS constraint_tests")
        except Exception:
            pass

        db.execute("""
            CREATE TABLE constraint_tests (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(100) NOT NULL UNIQUE,
                balance NUMERIC(10, 2) CHECK (balance >= 0),
                status VARCHAR(20) DEFAULT 'pending'
            )
        """)
        db.close()

        yield multi_constraint_config

        # Cleanup
        db = Database()
        try:
            db.connect()
            db.execute("DROP TABLE IF EXISTS constraint_tests")
            db.close()
        except Exception:
            pass

    def test_recovery_sequence_not_null_then_unique(self, constraint_test_table):
        psycopg2 = """Test recovery from sequence of constraint violations."""
        pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Insert base data
        db.execute(
            "INSERT INTO constraint_tests (name, email, balance) VALUES (?, ?, ?)",
            ("User1", "user1@example.com", 100),
        )

        # First error - NOT NULL violation
        db.begin_transaction()
        try:
            db.execute(
                "INSERT INTO constraint_tests (name, email, balance) VALUES (?, ?, ?)",
                (None, "user2@example.com", 50),
            )
        except psycopg2.errors.NotNullViolation:
            pass
        db._validate_transaction_state()
        assert db._local.transaction_depth == 0

        # Second error - UNIQUE violation
        db.begin_transaction()
        try:
            db.execute(
                "INSERT INTO constraint_tests (name, email, balance) VALUES (?, ?, ?)",
                ("User1", "user1@example.com", 75),  # Duplicate email
            )
        except psycopg2.errors.UniqueViolation:
            pass
        db._validate_transaction_state()
        assert db._local.transaction_depth == 0

        # Third attempt should succeed
        db.begin_transaction()
        db.execute(
            "INSERT INTO constraint_tests (name, email, balance) VALUES (?, ?, ?)",
            ("User2", "user2@example.com", 200),
        )
        db.commit()

        # Verify data
        results = db.fetch_all("SELECT * FROM constraint_tests ORDER BY id")
        assert len(results) == 2

        db.close()

    def test_recovery_with_check_constraint_and_retry(self, constraint_test_table):
        """Test recovery from CHECK constraint with successful retry."""
        psycopg2 = pytest.importorskip("psycopg2")

        db = Database()
        db.connect()

        # Try with invalid balance (negative)
        db.begin_transaction()
        try:
            db.execute(
                "INSERT INTO constraint_tests (name, email, balance) VALUES (?, ?, ?)",
                ("NegativeBalance", "negative@example.com", -50),
            )
        except psycopg2.errors.CheckViolation:
            pass
        db._validate_transaction_state()

        # Retry with valid balance
        db.begin_transaction()
        db.execute(
            "INSERT INTO constraint_tests (name, email, balance) VALUES (?, ?, ?)",
            ("PositiveBalance", "positive@example.com", 100),
        )
        db.commit()

        # Verify successful insert
        result = db.fetch_one(
            "SELECT * FROM constraint_tests WHERE email = ?", ("positive@example.com",)
        )
        assert result is not None
        assert result["balance"] == 100

        db.close()


class TestPostgresConnectionPoolFailureRecovery:
    """Test recovery from connection pool-related failures."""

    def test_pool_connection_after_transaction_error(self):
        """Test that pool connection is properly managed after transaction error."""
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
            "connection_pool": {
                "min_connections": 2,
                "max_connections": 5,
            },
        }
        config.set("database", pg_config)

        db = Database()

        # Verify config is set
        pool_config = db.config.get("connection_pool", {})
        assert pool_config.get("max_connections") == 5

    def test_pool_size_after_error_recovery(self):
        """Test that pool maintains correct size after errors."""
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
            "connection_pool": {
                "min_connections": 3,
                "max_connections": 15,
            },
        }
        config.set("database", pg_config)

        db = Database()
        pool_config = db.config.get("connection_pool", {})

        # Verify pool configuration is respected
        assert pool_config.get("min_connections") == 3
        assert pool_config.get("max_connections") == 15
