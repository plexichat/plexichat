"""
Shared fixtures for API tests.
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
        "webhooks": {
            "max_webhooks_per_channel": 10,
            "max_webhooks_per_server": 50,
            "max_message_length": 2000,
            "max_embeds_per_message": 10,
        },
        "api": {
            "title": "PlexiChat API Test",
            "version": "1.0.0",
            "api_prefix": "/api/v1",
            "debug": True,
            "cors_origins": ["*"],
        },
    }


@pytest.fixture(scope="session")
def test_env():
    """Setup test environment once per session."""
    test_dir = "temp_api_test"

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
    from src.core import relationships
    from src.core import presence
    from src.core import reactions
    from src.core import embeds
    from src.core import webhooks
    import src.api as api

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

    relationships._manager = None
    relationships._setup_complete = False
    relationships.setup(db, auth, servers)

    presence._manager = None
    presence._setup_complete = False
    presence.setup(db, auth, relationships, servers)

    reactions._manager = None
    reactions._setup_complete = False
    reactions.setup(db, messaging, servers, relationships)

    embeds._manager = None
    embeds._setup_complete = False
    embeds.setup(db, messaging, servers)

    webhooks._manager = None
    webhooks._setup_complete = False
    webhooks.setup(db, auth, messaging, servers, embeds)

    api.setup(
        db=db,
        auth_module=auth,
        messaging_module=messaging,
        servers_module=servers,
        relationships_module=relationships,
        presence_module=presence,
        reactions_module=reactions,
        embeds_module=embeds,
        webhooks_module=webhooks,
    )

    yield {
        "db": db,
        "auth": auth,
        "messaging": messaging,
        "servers": servers,
        "relationships": relationships,
        "presence": presence,
        "reactions": reactions,
        "embeds": embeds,
        "webhooks": webhooks,
        "api": api,
    }

    db.close()
    gc.collect()


@pytest.fixture(scope="module")
def test_client(db_and_modules):
    """Create test client for API testing."""
    from fastapi.testclient import TestClient
    from src.api import create_app

    app = create_app()
    client = TestClient(app)

    yield client


@pytest.fixture(scope="module")
def test_user(db_and_modules):
    """Create a test user and return credentials."""
    auth = db_and_modules["auth"]
    unique_id = uuid.uuid4().hex[:8]

    user = auth.register(
        username=f"testuser_{unique_id}",
        email=f"testuser_{unique_id}@example.com",
        password="TestPass123!"
    )

    result = auth.login(
        username=f"testuser_{unique_id}",
        password="TestPass123!"
    )

    return {
        "user": user,
        "token": result.token,
        "username": f"testuser_{unique_id}",
        "password": "TestPass123!",
    }


@pytest.fixture(scope="module")
def test_server(db_and_modules, test_user):
    """Create a test server."""
    servers = db_and_modules["servers"]
    unique_id = uuid.uuid4().hex[:8]

    server = servers.create_server(
        owner_id=test_user["user"].id,
        name=f"Test Server {unique_id}"
    )

    channels = servers.get_channels(test_user["user"].id, server.id)
    channel = channels[0] if channels else None

    return {
        "server": server,
        "channel": channel,
    }


@pytest.fixture
def auth_headers(test_user):
    """Get authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {test_user['token']}"}
