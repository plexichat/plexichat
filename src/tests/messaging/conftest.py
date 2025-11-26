"""
Shared fixtures for messaging tests.
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
utils_path = os.path.join(project_root, "src", "utils")

for path in [project_root, src_path, common_utils_path, utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

import utils.config as config
import utils.logger as logger
from encryption import (
    setup as encryption_setup,
    generate_snowflake_id,
)


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
        "messaging": {
            "max_message_length": 4000,
            "max_group_participants": 100,
            "max_attachment_size": 10485760,
            "max_attachments_per_message": 10,
            "dm_auto_create": True,
            "encrypt_messages": False,  # Disable for easier testing
            "encrypt_attachments": False,
            "message_preview_length": 100,
        },
    }


@pytest.fixture(scope="session")
def test_env():
    """Setup test environment once per session."""
    test_dir = "temp_messaging_test"
    
    # Try to clean up, ignore errors on Windows
    try:
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir, ignore_errors=True)
    except Exception:
        pass
    
    os.makedirs(test_dir, exist_ok=True)
    
    # Setup logger once - use unique log dir to avoid conflicts
    log_dir = os.path.join(test_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    try:
        logger.setup(log_dir=log_dir, level="WARNING", zip_logs=False)
    except Exception:
        # Logger may already be setup from another test session
        pass
    
    # Setup encryption
    encryption_setup(worker_id=1, datacenter_id=1)
    
    yield test_dir
    
    # Cleanup at end of session
    import gc
    gc.collect()


@pytest.fixture(scope="module")
def db_and_modules(test_env, request):
    """Setup database, auth, and messaging once per test module."""
    import gc
    
    module_name = request.module.__name__.split(".")[-1]
    config_path = os.path.join(test_env, f"config_{module_name}.yaml")
    db_path = os.path.join(test_env, f"test_{module_name}.db")
    
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    
    default_config = get_test_config()
    default_config["database"] = {"type": "sqlite", "path": db_path}
    
    config.setup(config_path=config_path, default_config=default_config)
    
    from src.core.database import Database
    from src.core import auth
    from src.core import messaging
    
    db = Database()
    db.connect()
    
    # Re-initialize auth
    auth._manager = None
    auth._setup_complete = False
    auth.setup(db)
    
    # Re-initialize messaging
    messaging._manager = None
    messaging._setup_complete = False
    messaging.setup(db, auth)
    
    yield db, auth, messaging
    
    db.close()
    gc.collect()


@pytest.fixture
def users(db_and_modules):
    """Create test users."""
    db, auth, messaging = db_and_modules
    
    unique_id = uuid.uuid4().hex[:8]
    
    user1 = auth.register(
        username=f"user1_{unique_id}",
        email=f"user1_{unique_id}@example.com",
        password="TestPass123!"
    )
    
    user2 = auth.register(
        username=f"user2_{unique_id}",
        email=f"user2_{unique_id}@example.com",
        password="TestPass123!"
    )
    
    user3 = auth.register(
        username=f"user3_{unique_id}",
        email=f"user3_{unique_id}@example.com",
        password="TestPass123!"
    )
    
    return user1, user2, user3, messaging


@pytest.fixture
def dm_conversation(users):
    """Create a DM conversation between user1 and user2."""
    user1, user2, user3, messaging = users
    
    dm = messaging.create_dm(user1.id, user2.id)
    
    return dm, user1, user2, messaging


@pytest.fixture
def group_conversation(users):
    """Create a group conversation with user1 as owner."""
    user1, user2, user3, messaging = users
    
    group = messaging.create_group(
        owner_id=user1.id,
        name="Test Group",
        participant_ids=[user2.id, user3.id]
    )
    
    return group, user1, user2, user3, messaging
