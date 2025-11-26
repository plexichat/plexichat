"""
Shared fixtures for auth tests.
"""

import pytest
import os
import sys
import shutil
import uuid

# Setup paths
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

import utils.config as config
import utils.logger as logger


def get_test_config():
    """Get default test configuration."""
    return {
        "authentication": {
            "accounts": {
                "allow_registration": True,
                "require_email_verification": False,
                "max_bots_per_user": 5,
                "username_min_length": 3,
                "username_max_length": 32,
            },
            "sessions": {
                "token_bytes": 32,
                "expire_hours": 168,
                "max_per_user": 10,
                "extend_on_activity": True,
                "extend_threshold_hours": 24,
            },
            "security": {
                "max_failed_attempts": 3,
                "lockout_duration_minutes": 1,
            },
            "totp": {
                "enabled": True,
                "issuer": "TestApp",
                "digits": 6,
                "interval": 30,
                "backup_code_count": 5,
                "backup_code_length": 8,
            },
            "password": {
                "min_length": 8,
                "max_length": 128,
                "require_uppercase": True,
                "require_lowercase": True,
                "require_digit": True,
                "require_special": True,
            },
            "bots": {
                "token_bytes": 48,
                "require_owner_2fa": False,
            },
        },
    }


@pytest.fixture(scope="session")
def test_env():
    """Setup test environment once per session."""
    test_dir = "temp_auth_test"
    
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir, ignore_errors=True)
    os.makedirs(test_dir, exist_ok=True)
    
    # Setup logger once
    log_dir = os.path.join(test_dir, "logs")
    logger.setup(log_dir=log_dir, level="WARNING")  # Less verbose for tests
    
    yield test_dir
    
    # Cleanup at end of session
    import gc
    gc.collect()


@pytest.fixture(scope="module")
def db_and_auth(test_env, request):
    """Setup database and auth once per test module."""
    import gc
    
    # Use module name for unique db file
    module_name = request.module.__name__.split(".")[-1]
    config_path = os.path.join(test_env, f"config_{module_name}.yaml")
    db_path = os.path.join(test_env, f"test_{module_name}.db")
    
    # Remove old db file
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    
    # Setup config
    default_config = get_test_config()
    default_config["database"] = {"type": "sqlite", "path": db_path}
    
    config.setup(config_path=config_path, default_config=default_config)
    
    from src.core.database import Database
    from src.core import auth
    
    db = Database()
    db.connect()
    
    # Re-initialize auth for this module
    auth._manager = None
    auth._setup_complete = False
    auth.setup(db)
    
    yield db, auth
    
    # Cleanup
    db.close()
    gc.collect()


@pytest.fixture
def registered_user(db_and_auth, request):
    """Create a registered user for tests with unique name."""
    db, auth = db_and_auth
    
    # Generate unique username
    unique_id = uuid.uuid4().hex[:8]
    username = f"user_{unique_id}"
    email = f"{unique_id}@example.com"
    
    user = auth.register(
        username=username,
        email=email,
        password="TestPass123!"
    )
    
    return user, auth, username


@pytest.fixture
def logged_in_user(registered_user):
    """Create a logged in user with token."""
    user, auth, username = registered_user
    
    result = auth.login(
        username=username,
        password="TestPass123!"
    )
    
    return user, result.token, auth, username
