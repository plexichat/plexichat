"""
Database management for tests.

Provides session-scoped database with transaction-based isolation.
"""

import os
import shutil


class DatabaseManager:
    """
    Manages test database lifecycle.
    
    Creates a single database per test session and provides
    transaction-based isolation between tests.
    """

    def __init__(self, test_dir: str = "temp/test_session"):
        self.test_dir = test_dir
        self.db_path = os.path.join(test_dir, "test_session.db")
        self._db = None
        self._in_transaction = False

    def setup(self):
        """
        Initialize the test database.
        
        Creates the test directory, database file, and connects.
        Should be called once per test session.
        """
        # Clean up any existing test directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

        os.makedirs(self.test_dir, exist_ok=True)

        # Create logs directory for logger
        log_dir = os.path.join(self.test_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        # Import and setup dependencies
        import sys
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
        src_path = os.path.join(project_root, "src")
        utils_path = os.path.join(project_root, "src", "utils")
        common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

        for path in [project_root, src_path, utils_path, common_utils_path]:
            if path not in sys.path:
                sys.path.insert(0, path)

        # Setup logger (suppress most output during tests)
        import utils.logger as logger
        try:
            logger.setup(log_dir=log_dir, level="WARNING", zip_logs=False)
        except Exception:
            pass  # Logger may already be setup

        # Setup encryption
        from encryption import setup as encryption_setup
        encryption_setup(worker_id=1, datacenter_id=1)

        # Setup config
        import utils.config as config
        from .config import get_test_config

        config_path = os.path.join(self.test_dir, "config.yaml")
        default_config = get_test_config()
        default_config["database"] = {"type": "sqlite", "path": self.db_path}
        config.setup(config_path=config_path, default_config=default_config)

        # Create database
        from src.core.database import Database
        self._db = Database()
        self._db.connect()

        return self._db

    @property
    def db(self):
        """Get the database instance."""
        if self._db is None:
            raise RuntimeError("Database not initialized. Call setup() first.")
        return self._db

    def begin_transaction(self):
        """Start a new transaction for test isolation."""
        if not self._in_transaction and self._db is not None:
            self._db.execute("BEGIN")
            self._in_transaction = True

    def rollback_transaction(self):
        """Rollback the current transaction to restore state."""
        if self._in_transaction and self._db is not None:
            self._db.execute("ROLLBACK")
            self._in_transaction = False

    def commit_transaction(self):
        """Commit the current transaction (rarely needed in tests)."""
        if self._in_transaction and self._db is not None:
            self._db.execute("COMMIT")
            self._in_transaction = False

    def teardown(self):
        """
        Clean up the test database.
        
        Should be called once at end of test session.
        """
        if self._db:
            try:
                if self._in_transaction:
                    self._db.execute("ROLLBACK")
            except Exception:
                pass

            try:
                self._db.close()
            except Exception:
                pass

            self._db = None

        # Clean up test directory
        import gc
        gc.collect()

        try:
            if os.path.exists(self.test_dir):
                shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass
