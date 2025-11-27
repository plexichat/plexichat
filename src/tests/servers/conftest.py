"""
Shared fixtures for server tests.
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
        "messaging": {
            "max_message_length": 4000,
            "max_group_participants": 100,
            "max_attachment_size": 10485760,
            "max_attachments_per_message": 10,
            "dm_auto_create": True,
            "encrypt_messages": False,
            "encrypt_attachments": False,
            "message_preview_length": 100,
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
    }


@pytest.fixture(scope="session")
def test_env():
    """Setup test environment once per session."""
    test_dir = "temp_servers_test"

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
    """Setup database, auth, messaging, and servers once per test module."""
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
    from src.core import servers

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

    # Re-initialize servers
    servers._manager = None
    servers._setup_complete = False
    servers.setup(db, auth, messaging)

    yield db, auth, messaging, servers

    db.close()
    gc.collect()


@pytest.fixture(scope="module")
def base_users(db_and_modules):
    """Create base test users once per module."""
    db, auth, messaging, servers = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    user1 = auth.register(
        username=f"owner_{unique_id}",
        email=f"owner_{unique_id}@example.com",
        password="TestPass123!"
    )

    user2 = auth.register(
        username=f"admin_{unique_id}",
        email=f"admin_{unique_id}@example.com",
        password="TestPass123!"
    )

    user3 = auth.register(
        username=f"member_{unique_id}",
        email=f"member_{unique_id}@example.com",
        password="TestPass123!"
    )

    user4 = auth.register(
        username=f"outsider_{unique_id}",
        email=f"outsider_{unique_id}@example.com",
        password="TestPass123!"
    )

    return user1, user2, user3, user4, auth, messaging, servers


@pytest.fixture
def users(base_users):
    """Get test users."""
    user1, user2, user3, user4, auth, messaging, servers = base_users
    return user1, user2, user3, user4, servers


@pytest.fixture
def server_with_members(base_users):
    """Create a server with owner, admin, and member."""
    owner, admin_user, member_user, outsider, auth, messaging, servers = base_users

    unique_id = uuid.uuid4().hex[:6]
    server = servers.create_server(
        owner_id=owner.id,
        name=f"Test Server {unique_id}",
        description="A test server"
    )

    # Add admin and member
    servers.add_member(server.id, admin_user.id)
    servers.add_member(server.id, member_user.id)

    # Create admin role and assign
    admin_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Admin",
        permissions={
            "administrator": False,
            "channels.manage": True,
            "members.kick": True,
            "members.ban": True,
            "members.manage_roles": True,
            "messages.manage": True,
        },
        color="#FF0000",
        hoist=True
    )

    servers.assign_role(owner.id, server.id, admin_user.id, admin_role.id)

    return server, owner, admin_user, member_user, outsider, admin_role, servers


@pytest.fixture
def fresh_server(base_users):
    """Create a fresh server for tests needing isolation."""
    owner, admin_user, member_user, outsider, auth, messaging, servers = base_users

    unique_id = uuid.uuid4().hex[:6]
    server = servers.create_server(
        owner_id=owner.id,
        name=f"Fresh Server {unique_id}",
        description="A fresh test server"
    )

    return server, owner, servers


@pytest.fixture
def server_with_channels(server_with_members):
    """Create a server with multiple channels."""
    server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

    # Create category
    category = servers.create_category(
        user_id=owner.id,
        server_id=server.id,
        name="Text Channels"
    )

    # Create channels
    general = servers.get_channels(owner.id, server.id)[0]  # Default general channel

    announcements = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="announcements",
        category_id=category.id
    )

    private = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="private",
        category_id=category.id
    )

    return server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers
