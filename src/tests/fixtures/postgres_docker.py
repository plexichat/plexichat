"""
PostgreSQL Docker Fixtures for Testing.

This module provides pytest fixtures for setting up PostgreSQL databases
in Docker containers for integration testing. Supports both local development
and CI environments.

Features:
- Automatic Docker container lifecycle management
- Support for testcontainers-python for container orchestration
- Fallback to docker SDK for environments without testcontainers
- Configuration discovery from environment variables
- Automatic cleanup on test completion

Usage:
    def test_with_postgres(postgres_container):
        db = Database()
        db.connect()
        # Test code here
        db.close()

Environment Variables:
    POSTGRES_HOST: Override default localhost
    POSTGRES_PORT: Override default 5432
    POSTGRES_USER: Override default postgres
    POSTGRES_PASSWORD: Override default postgres
    POSTGRES_DB: Override default test_db
    USE_DOCKER: Force Docker usage (true/false)
    TESTCONTAINERS_DOCKER_SOCKET: Docker socket path for testcontainers
"""

import os
import time
import logging
import pytest
from typing import Dict, Optional, Generator, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.database.core import Database

logger = logging.getLogger(__name__)


class PostgresDockerManager:
    """Manages PostgreSQL Docker container lifecycle."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "postgres",
        dbname: str = "test_db",
        image: str = "postgres:15-alpine",
        use_docker: bool = True,
    ):
        """Initialize PostgreSQL Docker manager.

        Args:
            host: PostgreSQL host address
            port: PostgreSQL port
            user: PostgreSQL user
            password: PostgreSQL password
            dbname: Database name
            image: Docker image to use
            use_docker: Whether to use Docker (vs local PostgreSQL)
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.dbname = dbname
        self.image = image
        self.use_docker = use_docker
        self.container_id: Optional[str] = None
        self.container = None
        self._docker_client = None

    def get_config(self) -> Dict[str, any]:
        """Get PostgreSQL configuration dictionary."""
        return {
            "type": "postgres",
            "postgres": {
                "host": self.host,
                "port": self.port,
                "user": self.user,
                "password": self.password,
                "dbname": self.dbname,
            },
            "connection_pool": {
                "min_connections": 2,
                "max_connections": 10,
                "connect_timeout": 10,
            },
        }

    def start(self) -> bool:
        """Start PostgreSQL Docker container.

        Attempts to use testcontainers if available, falls back to docker SDK.

        Returns:
            True if successful, False otherwise
        """
        if not self.use_docker:
            logger.info("Docker disabled - assuming local PostgreSQL is available")
            return self._check_local_postgres()

        try:
            # Try testcontainers first
            return self._start_with_testcontainers()
        except ImportError:
            logger.debug("testcontainers not available, trying docker SDK")
            try:
                return self._start_with_docker_sdk()
            except ImportError:
                logger.warning("Neither testcontainers nor docker SDK available")
                return self._check_local_postgres()
        except Exception as e:
            logger.error(f"Failed to start Docker container: {e}")
            return self._check_local_postgres()

    def _start_with_testcontainers(self) -> bool:
        """Start container using testcontainers-python.

        This is the preferred method as it provides better resource management.
        """
        try:
            from testcontainers.postgres import PostgresContainer
        except ImportError:
            raise ImportError("testcontainers not installed")

        try:
            logger.info(
                f"Starting PostgreSQL container using testcontainers ({self.image})"
            )

            self.container = (
                PostgresContainer(self.image)
                .with_environment("POSTGRES_USER", self.user)
                .with_environment("POSTGRES_PASSWORD", self.password)
                .with_environment("POSTGRES_DB", self.dbname)
                .with_bind_ports(self.port, 5432)
            )

            self.container.start()

            # Extract actual connection details from container
            self.host = self.container.get_container_host_ip()
            self.port = self.container.get_exposed_port(5432)

            logger.info(f"PostgreSQL container started: {self.host}:{self.port}")

            # Wait for container to be ready
            self._wait_for_postgres(timeout=30)

            return True
        except Exception as e:
            logger.error(f"testcontainers startup failed: {e}")
            raise

    def _start_with_docker_sdk(self) -> bool:
        """Start container using docker SDK (docker-py).

        Fallback method when testcontainers is not available.
        """
        try:
            import docker
        except ImportError:
            raise ImportError("docker SDK not installed")

        try:
            logger.info(
                f"Starting PostgreSQL container using docker SDK ({self.image})"
            )

            client = docker.from_env()
            self._docker_client = client

            # Pull image if not present
            try:
                client.images.get(self.image)
            except docker.errors.ImageNotFound:
                logger.info(f"Pulling Docker image: {self.image}")
                client.images.pull(self.image)

            # Start container
            self.container = client.containers.run(
                self.image,
                detach=True,
                environment={
                    "POSTGRES_USER": self.user,
                    "POSTGRES_PASSWORD": self.password,
                    "POSTGRES_DB": self.dbname,
                },
                ports={"5432/tcp": self.port},
                name=f"pytest-postgres-{int(time.time())}",
                remove=True,
            )

            self.container_id = self.container.id
            logger.info(f"PostgreSQL container started: {self.container_id}")

            # Wait for container to be ready
            self._wait_for_postgres(timeout=30)

            return True
        except Exception as e:
            logger.error(f"docker SDK startup failed: {e}")
            raise

    def _check_local_postgres(self) -> bool:
        """Check if local PostgreSQL is available.

        Returns:
            True if PostgreSQL is reachable, False otherwise
        """
        try:
            import psycopg2

            logger.info(f"Checking local PostgreSQL at {self.host}:{self.port}")

            conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database="postgres",
                connect_timeout=5,
            )
            conn.close()

            logger.info("Local PostgreSQL is available")
            return True
        except Exception as e:
            logger.error(f"Local PostgreSQL check failed: {e}")
            return False

    def _wait_for_postgres(self, timeout: int = 30) -> None:
        """Wait for PostgreSQL to be ready for connections.

        Args:
            timeout: Maximum time to wait in seconds

        Raises:
            TimeoutError if PostgreSQL doesn't become available
        """
        import psycopg2

        start_time = time.time()
        last_error = None

        while time.time() - start_time < timeout:
            try:
                conn = psycopg2.connect(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database="postgres",
                    connect_timeout=5,
                )
                conn.close()
                logger.info("PostgreSQL is ready for connections")
                return
            except Exception as e:
                last_error = e
                time.sleep(1)

        raise TimeoutError(f"PostgreSQL not ready after {timeout}s: {last_error}")

    def stop(self) -> None:
        """Stop and remove PostgreSQL Docker container."""
        if self.container is None:
            return

        try:
            if hasattr(self.container, "stop"):
                # testcontainers container
                logger.info("Stopping PostgreSQL container (testcontainers)")
                self.container.stop()
            else:
                # docker SDK container
                logger.info(f"Stopping PostgreSQL container: {self.container_id}")
                self.container.stop(timeout=10)
                self.container.remove()
        except Exception as e:
            logger.error(f"Error stopping container: {e}")
        finally:
            self.container = None
            self.container_id = None


# Global manager instance
_postgres_manager: Optional[PostgresDockerManager] = None


def get_postgres_manager() -> PostgresDockerManager:
    """Get or create global PostgreSQL Docker manager.

    Returns:
        PostgresDockerManager instance
    """
    global _postgres_manager

    if _postgres_manager is None:
        # Read configuration from environment
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        dbname = os.getenv("POSTGRES_DB", "test_db")
        image = os.getenv("POSTGRES_IMAGE", "postgres:15-alpine")
        use_docker = os.getenv("USE_DOCKER", "true").lower() == "true"

        _postgres_manager = PostgresDockerManager(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname,
            image=image,
            use_docker=use_docker,
        )

    return _postgres_manager


# ============================================================================
# Pytest Fixtures
# ============================================================================


@pytest.fixture(scope="session")
def postgres_manager() -> Generator[PostgresDockerManager, None, None]:
    """Session-scoped fixture that starts PostgreSQL for the test session.

    Yields:
        PostgresDockerManager instance
    """
    manager = get_postgres_manager()

    if not manager.start():
        pytest.skip("PostgreSQL not available")

    yield manager

    manager.stop()


@pytest.fixture(scope="session")
def postgres_config(postgres_manager) -> Dict[str, any]:
    """Get PostgreSQL configuration for all tests.

    Args:
        postgres_manager: Fixture that starts PostgreSQL

    Yields:
        PostgreSQL configuration dictionary
    """
    yield postgres_manager.get_config()


@pytest.fixture
def postgres_db(postgres_config):
    """Get configured Database instance connected to PostgreSQL.

    This fixture automatically sets up the configuration and provides
    a Database instance ready for use.

    Args:
        postgres_config: PostgreSQL configuration

    Yields:
        Database instance (automatically cleaned up)
    """
    import utils.config as config
    from src.core.database.core import Database

    # Apply configuration
    config.set("database", postgres_config)

    # Create and connect database
    db = Database()
    try:
        db.connect()
    except Exception as e:
        pytest.skip(f"Could not connect to PostgreSQL: {e}")

    yield db

    # Cleanup
    try:
        db.close()
    except Exception:
        pass


@pytest.fixture
def postgres_db_with_table(postgres_db):
    """Database instance with a test table for transaction tests.

    Args:
        postgres_db: Connected Database instance

    Yields:
        Database instance with test_transactions table
    """
    # Create test table
    postgres_db.execute("""
        DROP TABLE IF EXISTS test_transactions CASCADE
    """)

    postgres_db.execute("""
        CREATE TABLE test_transactions (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            value INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    yield postgres_db

    # Cleanup
    try:
        postgres_db.execute("DROP TABLE IF EXISTS test_transactions CASCADE")
    except Exception:
        pass


@pytest.fixture
def postgres_db_with_constraints(postgres_db):
    """Database instance with a constrained table for error recovery tests.

    Args:
        postgres_db: Connected Database instance

    Yields:
        Database instance with constrained_data table
    """
    # Create table with various constraints
    postgres_db.execute("""
        DROP TABLE IF EXISTS constrained_data CASCADE
    """)

    postgres_db.execute("""
        CREATE TABLE constrained_data (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            email VARCHAR(100) NOT NULL,
            age INTEGER CHECK (age >= 0 AND age <= 150),
            balance NUMERIC(10, 2) CHECK (balance >= 0),
            status VARCHAR(20) DEFAULT 'active',
            CONSTRAINT valid_email CHECK (email LIKE '%@%.%')
        )
    """)

    yield postgres_db

    # Cleanup
    try:
        postgres_db.execute("DROP TABLE IF EXISTS constrained_data CASCADE")
    except Exception:
        pass


@pytest.fixture
def clean_postgres_db(postgres_db) -> "Database":
    """Database instance with automatic table cleanup between tests.

    Removes all tables before each test to ensure clean state.

    Args:
        postgres_db: Connected Database instance

    Yields:
        Database instance with clean state
    """
    # Get all tables
    result = postgres_db.fetch_all("""
        SELECT tablename FROM pg_tables 
        WHERE schemaname = 'public'
    """)

    # Drop all tables
    for row in result:
        table_name = row["tablename"]
        try:
            postgres_db.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
        except Exception as e:
            logger.warning(f"Could not drop table {table_name}: {e}")

    yield postgres_db

    # Cleanup after test
    result = postgres_db.fetch_all("""
        SELECT tablename FROM pg_tables 
        WHERE schemaname = 'public'
    """)

    for row in result:
        table_name = row["tablename"]
        try:
            postgres_db.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
        except Exception:
            pass


@pytest.fixture
def postgres_connection_pool_tester(postgres_db):
    """Helper for testing connection pool behavior.

    Args:
        postgres_db: Connected Database instance

    Yields:
        Dictionary with pool testing utilities
    """

    class PoolTester:
        """Helper class for testing connection pool."""

        def __init__(self, db):
            self.db = db
            self.connections_acquired = []
            self.connections_released = []

        def get_pool_status(self) -> Dict[str, any]:
            """Get current pool status."""
            return self.db.get_pool_stats()

        def acquire_multiple(self, count: int) -> list:
            """Acquire multiple connections (for testing pool exhaustion)."""
            conns = []
            for _ in range(count):
                # Force new connection by creating new Database instance
                new_db = type(self.db)()
                try:
                    new_db.connect()
                    conns.append(new_db)
                except Exception as e:
                    logger.warning(f"Failed to acquire connection: {e}")
                    break
            return conns

        def release_all(self, conns: list) -> None:
            """Release all connections."""
            for conn_obj in conns:
                try:
                    conn_obj.close()
                except Exception as e:
                    logger.warning(f"Failed to release connection: {e}")

    yield PoolTester(postgres_db)
