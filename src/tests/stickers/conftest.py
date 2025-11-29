"""
Shared fixtures for sticker tests.
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
            },
            "password": {
                "min_length": 8,
                "max_length": 128,
                "require_uppercase": True,
                "require_lowercase": True,
                "require_digit": True,
                "require_special": True,
            },
        },
        "messaging": {
            "max_message_length": 4000,
            "encrypt_messages": False,
        },
        "servers": {
            "max_servers_per_user": 100,
            "max_channels_per_server": 500,
        },
        "stickers": {
            "max_packs_per_server": 50,
            "max_stickers_per_pack": 50,
            "max_sticker_size": 524288,
            "max_sticker_name_length": 30,
            "max_pack_name_length": 50,
            "allowed_formats": ["png", "apng", "json"],
            "max_suggestions": 10,
        },
    }


@pytest.fixture(scope="session")
def test_env():
    """Setup test environment once per session."""
    test_dir = "temp/stickers"

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
    from src.core import stickers

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

    stickers._manager = None
    stickers._setup_complete = False
    stickers.setup(db, messaging, servers)

    yield db, auth, messaging, servers, stickers

    db.close()
    gc.collect()


@pytest.fixture
def server_with_owner(db_and_modules):
    """Create a server with owner for sticker tests."""
    db, auth, messaging, servers, stickers = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    owner = auth.register(
        username=f"owner_{unique_id}",
        email=f"owner_{unique_id}@example.com",
        password="TestPass123!"
    )

    server = servers.create_server(owner.id, f"Test Server {unique_id}")

    return owner, server, stickers, servers


@pytest.fixture
def server_with_pack(server_with_owner):
    """Create a server with a sticker pack."""
    owner, server, stickers, servers = server_with_owner

    pack = stickers.create_pack(
        user_id=owner.id,
        name="Test Pack",
        description="Test sticker pack",
        server_id=server.id
    )

    return owner, server, pack, stickers, servers
