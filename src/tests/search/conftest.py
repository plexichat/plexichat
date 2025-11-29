"""
Shared fixtures for search tests.
"""

import pytest
import os
import sys
import shutil
import uuid

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
        "search": {
            "backend": "sqlite_fts5",
            "batch_size": 100,
            "write_time_indexing": True,
            "result_limit": 100,
            "discovery": {
                "min_members_for_listing": 2,
                "bump_cooldown_hours": 0,
                "max_tags": 10,
            },
        },
    }


@pytest.fixture(scope="session")
def test_env():
    """Setup test environment once per session."""
    test_dir = "temp_search_test"

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
    from src.core import messaging
    from src.core import servers
    from src.core import search

    db = Database()
    db.connect()

    auth._manager = None
    auth._setup_complete = False
    auth.setup(db)

    messaging._manager = None
    messaging._setup_complete = False
    messaging.setup(db, auth)

    servers._manager = None
    servers._setup_complete = False
    servers.setup(db, auth, messaging)

    search._manager = None
    search._setup_complete = False
    search.setup(db, auth, messaging, servers)

    yield db, auth, messaging, servers, search

    db.close()
    gc.collect()


@pytest.fixture(scope="module")
def base_users(db_and_modules):
    """Create base test users once per module."""
    db, auth, messaging, servers, search = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    user1 = auth.register(
        username=f"alice_{unique_id}",
        email=f"alice_{unique_id}@example.com",
        password="TestPass123!"
    )

    user2 = auth.register(
        username=f"bob_{unique_id}",
        email=f"bob_{unique_id}@example.com",
        password="TestPass123!"
    )

    user3 = auth.register(
        username=f"charlie_{unique_id}",
        email=f"charlie_{unique_id}@example.com",
        password="TestPass123!"
    )

    return user1, user2, user3, auth, messaging, servers, search


@pytest.fixture
def users_with_dm(base_users):
    """Create users with a DM conversation and messages."""
    user1, user2, user3, auth, messaging, servers, search = base_users

    dm = messaging.create_dm(user1.id, user2.id)
    
    msg1 = messaging.send_message(user1.id, dm.id, "Hello world from Alice")
    msg2 = messaging.send_message(user2.id, dm.id, "Hi Alice, this is Bob")
    msg3 = messaging.send_message(user1.id, dm.id, "Check out this link https://example.com")
    
    search.index_message(msg1.id, msg1.content, {
        "author_id": user1.id,
        "conversation_id": dm.id,
        "created_at": msg1.created_at,
    })
    search.index_message(msg2.id, msg2.content, {
        "author_id": user2.id,
        "conversation_id": dm.id,
        "created_at": msg2.created_at,
    })
    search.index_message(msg3.id, msg3.content, {
        "author_id": user1.id,
        "conversation_id": dm.id,
        "created_at": msg3.created_at,
        "has_links": True,
    })

    return user1, user2, dm, [msg1, msg2, msg3], search


@pytest.fixture
def users_with_server(db_and_modules):
    """Create users with a server for testing."""
    db, auth, messaging, servers, search = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    owner = auth.register(
        username=f"owner_{unique_id}",
        email=f"owner_{unique_id}@example.com",
        password="TestPass123!"
    )

    member1 = auth.register(
        username=f"member1_{unique_id}",
        email=f"member1_{unique_id}@example.com",
        password="TestPass123!"
    )

    member2 = auth.register(
        username=f"member2_{unique_id}",
        email=f"member2_{unique_id}@example.com",
        password="TestPass123!"
    )

    server = servers.create_server(owner.id, f"Test Server {unique_id}")
    servers.add_member(server.id, member1.id)
    servers.add_member(server.id, member2.id)

    return owner, member1, member2, server, servers, search


@pytest.fixture
def indexed_users(db_and_modules):
    """Create and index users for user search tests."""
    db, auth, messaging, servers, search = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    users = []
    for name in ["alice", "bob", "charlie", "david", "eve"]:
        user = auth.register(
            username=f"{name}_{unique_id}",
            email=f"{name}_{unique_id}@example.com",
            password="TestPass123!"
        )
        search._get_manager()._indexer.index_user(
            search.models.IndexedUser(
                user_id=user.id,
                username=user.username,
                display_name=name.capitalize(),
            )
        )
        users.append(user)

    return users, search
