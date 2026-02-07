"""
Tests for database connection pool management, including age-based eviction.
"""

import pytest
import os
import sys
import time
from unittest.mock import MagicMock

# Setup paths
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
src_path = project_root
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

import src.utils.config as config  # noqa: E402
import src.utils.logger as logger  # noqa: E402
from src.core.database.core import Database  # noqa: E402

@pytest.fixture(scope="module")
def setup_logging():
    if not os.path.exists("temp_test_pool"):
        os.makedirs("temp_test_pool")
    logger.setup(log_dir="temp_test_pool/logs", level="DEBUG")
    yield
    import shutil
    import time
    if os.path.exists("temp_test_pool"):
        # Give some time for files to be released
        time.sleep(0.5)
        try:
            shutil.rmtree("temp_test_pool")
        except PermissionError:
            # On Windows, log files might still be held open by the logger
            pass

def test_connection_age_eviction(setup_logging):
    """Test that connections exceeding max age are evicted."""
    db_path = "temp_test_pool/age_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        
    # Configure with very short max age (1 second)
    db_config = {
        "database": {
            "type": "sqlite",
            "path": db_path,
            "connection_pool": {
                "max_connection_age_hours": 1 / 3600.0  # 1 second
            }
        }
    }
    config.setup(config_path="temp_test_pool/config_age.yaml", default_config=db_config)
    
    db = Database()
    db.connect()
    
    first_conn = db._local.connection
    first_id = id(first_conn)
    assert first_conn is not None
    
    # Wait for connection to exceed max age
    time.sleep(1.5)
    
    # Next call to _get_conn should evict and create new connection
    second_conn = db._get_conn()
    second_id = id(second_conn)
    
    assert second_id != first_id
    assert second_conn is not None
    
    db.close()

def test_connection_idle_eviction(setup_logging):
    """Test that connections exceeding max idle time are evicted."""
    db_path = "temp_test_pool/idle_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        
    # Configure with very short max idle time (1 second)
    db_config = {
        "database": {
            "type": "sqlite",
            "path": db_path,
            "connection_pool": {
                "max_idle_time": 1,
                "max_connection_age_hours": 1  # 1 hour
            }
        }
    }
    config.setup(config_path="temp_test_pool/config_idle.yaml", default_config=db_config)
    
    db = Database()
    db.connect()
    
    first_conn = db._local.connection
    first_id = id(first_conn)
    
    # Wait for connection to exceed max idle time
    time.sleep(1.5)
    
    # Next call to _get_conn should evict and create new connection
    second_conn = db._get_conn()
    second_id = id(second_conn)
    
    assert second_id != first_id
    assert second_conn is not None
    
    db.close()

def test_close_forces_closure_for_old_connections(setup_logging):
    """Test that close() forces closure if connection is old."""
    db_path = "temp_test_pool/close_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        
    db_config = {
        "database": {
            "type": "sqlite",
            "path": db_path,
            "connection_pool": {
                "max_connection_age_hours": 1 / 3600.0  # 1 second
            }
        }
    }
    config.setup(config_path="temp_test_pool/config_close.yaml", default_config=db_config)
    
    db = Database()
    db.connect()
    
    # Mock engine.close_connection to verify close=True is passed
    db.engine.close_connection = MagicMock()
    
    # Wait for connection to exceed max age
    time.sleep(1.5)
    
    # Call close
    db.close()
    
    # Verify close_connection was called with close=True
    # The first arg is the connection, second is pool (None for sqlite), third is params
    db.engine.close_connection.assert_called_once()
    args, kwargs = db.engine.close_connection.call_args
    assert args[2] == {"close": True}

def test_monitoring_log_interval_config(setup_logging):
    """Test that monitoring log interval is correctly read from config."""
    db_config = {
        "database": {"type": "sqlite", "path": ":memory:"},
        "monitoring": {
            "log_interval": 42
        }
    }
    config.setup(config_path="temp_test_pool/config_mon.yaml", default_config=db_config)
    
    db = Database()
    assert db.monitor._periodic_log_interval == 42
    db.close()
