"""
Shared fixtures for presence tests.
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
from encryption import setup as encryption_setup


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
        "servers": {
            "max_servers_per_user": 100,
            "max_channels_per_server": 500,
            "max_roles_per_server": 250,
            "server_name_min_length": 2,
            "server_name_max_length": 100,
            "channel_name_min_length": 1,
            "channel_name_max_length": 100,
            "role_name_min_length": 1,
            "role_name_max_length": 100,
            "invite_code_length": 8,
        },
        "presence": {
            "typing_timeout_ms": 10000,
            "timeout_ms": 300000,
        },
    }


@pytest.fixture(scope="session")
def test_env():
    """Setup test environment once per session."""
    test_dir = "temp/presence"

    try:
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir, ignore_errors=True)
    except Exception:
        pass

    os.makedirs(test_dir, exist_ok=True)

    log_dir = os.path.join(test_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    try:
        logger.setup(log_dir=log_dir, level="WARNING", zip_logs=False)
    except Exception:
        pass

    encryption_setup(worker_id=1, datacenter_id=1)

    yield test_dir

    import gc
    gc.collect()


@pytest.fixture(scope="module")
def db_and_modules(test_env, request):
    """Setup database and all modules once per test module."""
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
    from src.core import servers
    from src.core import relationships
    from src.core import presence

    db = Database()
    db.connect()

    # Re-initialize auth
    auth._manager = None
    auth._setup_complete = False
    auth.setup(db)

    # Re-initialize servers
    servers._manager = None
    servers._setup_complete = False
    servers.setup(db, auth)

    # Re-initialize relationships
    relationships._manager = None
    relationships._setup_complete = False
    relationships.setup(db, auth, servers)

    # Re-initialize presence
    presence._manager = None
    presence._setup_complete = False
    presence.setup(db, auth, relationships, servers)

    yield db, auth, servers, relationships, presence

    db.close()
    gc.collect()


@pytest.fixture(scope="module")
def base_users(db_and_modules):
    """Create base test users once per module."""
    db, auth, servers, relationships, presence = db_and_modules

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

    user4 = auth.register(
        username=f"user4_{unique_id}",
        email=f"user4_{unique_id}@example.com",
        password="TestPass123!"
    )

    return user1, user2, user3, user4, auth, servers, relationships, presence


@pytest.fixture
def users(base_users):
    """Get test users."""
    user1, user2, user3, user4, auth, servers, relationships, presence = base_users
    return user1, user2, user3, user4, presence


@pytest.fixture
def fresh_users(db_and_modules):
    """Create fresh users for tests needing isolation."""
    db, auth, servers, relationships, presence = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    user1 = auth.register(
        username=f"fresh1_{unique_id}",
        email=f"fresh1_{unique_id}@example.com",
        password="TestPass123!"
    )

    user2 = auth.register(
        username=f"fresh2_{unique_id}",
        email=f"fresh2_{unique_id}@example.com",
        password="TestPass123!"
    )

    return user1, user2, presence


@pytest.fixture
def friends_pair(db_and_modules):
    """Create two users who are already friends."""
    db, auth, servers, relationships, presence = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    user1 = auth.register(
        username=f"friend1_{unique_id}",
        email=f"friend1_{unique_id}@example.com",
        password="TestPass123!"
    )

    user2 = auth.register(
        username=f"friend2_{unique_id}",
        email=f"friend2_{unique_id}@example.com",
        password="TestPass123!"
    )

    # Make them friends
    request = relationships.send_friend_request(user1.id, user2.id)
    relationships.accept_friend_request(user2.id, request.id)

    return user1, user2, relationships, presence


@pytest.fixture
def blocked_pair(db_and_modules):
    """Create two users where one has blocked the other."""
    db, auth, servers, relationships, presence = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    blocker = auth.register(
        username=f"blocker_{unique_id}",
        email=f"blocker_{unique_id}@example.com",
        password="TestPass123!"
    )

    blocked = auth.register(
        username=f"blocked_{unique_id}",
        email=f"blocked_{unique_id}@example.com",
        password="TestPass123!"
    )

    # Block the user
    relationships.block_user(blocker.id, blocked.id)

    return blocker, blocked, relationships, presence


@pytest.fixture
def users_with_server(db_and_modules):
    """Create users who share a server."""
    db, auth, servers, relationships, presence = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    user1 = auth.register(
        username=f"srv1_{unique_id}",
        email=f"srv1_{unique_id}@example.com",
        password="TestPass123!"
    )

    user2 = auth.register(
        username=f"srv2_{unique_id}",
        email=f"srv2_{unique_id}@example.com",
        password="TestPass123!"
    )

    user3 = auth.register(
        username=f"srv3_{unique_id}",
        email=f"srv3_{unique_id}@example.com",
        password="TestPass123!"
    )

    # Create server and add members
    server = servers.create_server(user1.id, f"Test Server {unique_id}")
    servers.add_member(server.id, user2.id)
    servers.add_member(server.id, user3.id)

    return user1, user2, user3, server, servers, presence
