"""
Tests for database connection pool management, including age-based eviction.
"""

import logging
import pytest
import os
import time
from unittest.mock import MagicMock

# common_utils is now a native package.


import src.utils.config as config
import src.utils.logger as logger
from src.core.database.core import Database


@pytest.fixture(scope="module")
def setup_logging(tmp_path_factory):
    temp_dir = tmp_path_factory.mktemp("test_pool")
    # ``logger.setup()`` mutates the process-global "AppLogger" singleton;
    # snapshot the level so this module's DEBUG setting cannot leak into later
    # tests in the same worker (where it inflates caplog output and can break
    # negative log assertions).
    app_logger = logging.getLogger("AppLogger")
    previous_level = app_logger.level
    logger.setup(log_dir=str(temp_dir / "logs"), level="DEBUG")
    try:
        yield temp_dir
    finally:
        app_logger.setLevel(previous_level)


def test_connection_age_eviction(setup_logging):
    """Test that connections exceeding max age are evicted."""
    temp_dir = setup_logging
    db_path = str(temp_dir / "age_test.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    # Configure with very short max age (0.5 seconds)
    db_config = {
        "database": {
            "type": "sqlite",
            "path": db_path,
            "connection_pool": {
                "max_connection_age_hours": 0.5 / 3600.0  # 0.5 seconds
            },
        }
    }
    config.setup(
        config_path=str(temp_dir / "config_age.yaml"), default_config=db_config
    )

    # ``Database`` is a process-wide singleton whose ``__init__`` runs only
    # once — drop it so this test's ``Database()`` re-initialises against the
    # short max-age config above instead of a stale earlier instance.
    Database.reset()

    db = Database()
    db.connect()

    first_conn = db._local.connection
    first_id = id(first_conn)
    assert first_conn is not None

    # Wait for connection to exceed max age (eviction is lazy on _get_conn)
    time.sleep(0.8)

    # Next call to _get_conn should evict and create new connection
    second_conn = db._get_conn()
    second_id = id(second_conn)

    assert second_id != first_id
    assert second_conn is not None

    db.close()


def test_connection_idle_eviction(setup_logging):
    """Test that connections exceeding max idle time are evicted."""
    temp_dir = setup_logging
    db_path = str(temp_dir / "idle_test.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    # Configure with very short max idle time (0.5 seconds)
    db_config = {
        "database": {
            "type": "sqlite",
            "path": db_path,
            "connection_pool": {
                "max_idle_time": 0.5,
                "max_connection_age_hours": 1,  # 1 hour
            },
        }
    }
    config.setup(
        config_path=str(temp_dir / "config_idle.yaml"), default_config=db_config
    )

    # ``Database`` is a process-wide singleton whose ``__init__`` runs only
    # once — drop it so this test's ``Database()`` re-initialises against the
    # short max-idle config above instead of a stale earlier instance.
    Database.reset()

    db = Database()
    db.connect()

    first_conn = db._local.connection
    first_id = id(first_conn)

    # Wait for connection to exceed max idle time (eviction is lazy on _get_conn)
    time.sleep(0.8)

    # Next call to _get_conn should evict and create new connection
    second_conn = db._get_conn()
    second_id = id(second_conn)

    assert second_id != first_id
    assert second_conn is not None

    db.close()


def test_close_forces_closure_for_old_connections(setup_logging):
    """Test that close() forces closure if connection is old."""
    temp_dir = setup_logging
    db_path = str(temp_dir / "close_test.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    db_config = {
        "database": {
            "type": "sqlite",
            "path": db_path,
            "connection_pool": {
                "max_connection_age_hours": 0.5 / 3600.0  # 0.5 seconds
            },
        }
    }
    config.setup(
        config_path=str(temp_dir / "config_close.yaml"), default_config=db_config
    )

    db = Database()
    db.connect()

    # Mock engine.close_connection to verify close=True is passed
    db.engine.close_connection = MagicMock()

    # Wait for connection to exceed max age
    time.sleep(0.8)

    # Call close
    db.close()

    # Verify close_connection was called with close=True
    # The first arg is the connection, second is pool (None for sqlite), third is params
    db.engine.close_connection.assert_called_once()
    args, kwargs = db.engine.close_connection.call_args
    assert args[2] == {"close": True}


def test_monitoring_log_interval_config(setup_logging):
    """Test that monitoring log interval is correctly read from config."""
    temp_dir = setup_logging
    db_config = {
        "database": {"type": "sqlite", "path": ":memory:"},
        "monitoring": {"log_interval": 42},
    }
    config.setup(
        config_path=str(temp_dir / "config_mon.yaml"), default_config=db_config
    )

    # ``Database`` is a process-wide singleton whose ``__init__`` runs only
    # once, so drop it here — otherwise ``Database()`` returns the instance
    # bound to whatever config an earlier test in the process used (interval
    # 30 instead of 42) and this test sees stale monitor settings.
    Database.reset()

    db = Database()
    assert db.monitor._periodic_log_interval == 42
    db.close()
