"""
Shared fixtures for poll tests.
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
        "polls": {
            "min_options": 2,
            "max_options": 10,
            "min_duration_hours": 1,
            "max_duration_hours": 168,
            "max_question_length": 300,
            "max_option_length": 100,
        },
    }


@pytest.fixture(scope="session")
def test_env():
    """Setup test environment once per session."""
    test_dir = "temp/polls"

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
    from src.core import polls

    db = Database()
    db.connect()

    auth._manager = None
    auth._setup_complete = False
    auth.setup(db)

    messaging._manager = None
    messaging._setup_complete = False
    messaging.setup(db, auth)

    polls._manager = None
    polls._setup_complete = False
    polls.setup(db, messaging)

    yield db, auth, messaging, polls

    db.close()
    gc.collect()


@pytest.fixture
def dm_with_message(db_and_modules):
    """Create a DM with a message for poll tests."""
    db, auth, messaging, polls = db_and_modules

    unique_id = uuid.uuid4().hex[:8]

    user1 = auth.register(
        username=f"poll_user1_{unique_id}",
        email=f"poll_user1_{unique_id}@example.com",
        password="TestPass123!"
    )

    user2 = auth.register(
        username=f"poll_user2_{unique_id}",
        email=f"poll_user2_{unique_id}@example.com",
        password="TestPass123!"
    )

    dm = messaging.create_dm(user1.id, user2.id)
    msg = messaging.send_message(user1.id, dm.id, "Poll message")

    return user1, user2, dm, msg, polls, messaging


@pytest.fixture
def poll_with_options(dm_with_message):
    """Create a poll with options."""
    user1, user2, dm, msg, polls, messaging = dm_with_message

    poll = polls.create_poll(
        user_id=user1.id,
        message_id=msg.id,
        question="What is your favorite color?",
        options=["Red", "Blue", "Green", "Yellow"]
    )

    return user1, user2, poll, polls, messaging
