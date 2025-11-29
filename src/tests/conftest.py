"""
Root conftest.py - Shared fixtures for all tests.

This file provides session-scoped database and module initialization
to dramatically reduce test execution time. Instead of creating a new
database for each test file (117 times!), we create ONE database per
session and reuse a pool of pre-created users.

Performance Strategy:
- ONE database for all tests (session-scoped)
- Pre-create a pool of users at session start (~5 seconds with real Argon2)
- Reuse pool users for tests that don't need fresh users
- Only create fresh users for tests that specifically need them
- NO mock hashing - all tests use real Argon2id

Usage:
    # For tests that can reuse users (most tests):
    def test_something(modules, user_factory):
        user = user_factory.create()  # Gets user from pool
        
    # For tests that need fresh users (registration, password change, etc.):
    def test_registration(modules):
        user = modules.auth.register(...)  # Creates new user with real hashing
"""

import pytest
import os
import sys
import uuid

# Ensure src is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, utils_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

from src.tests.fixtures.config import get_test_config, TEST_PASSWORD
from src.tests.fixtures.database import DatabaseManager
from src.tests.fixtures.modules import ModuleRegistry


# =============================================================================
# Session-Scoped Fixtures (initialized once per test run)
# =============================================================================

@pytest.fixture(scope="session")
def db_manager():
    """
    Create and manage the test database for the entire session.
    
    This is the key optimization - ONE database for all tests.
    """
    manager = DatabaseManager(test_dir="temp/test_session")
    manager.setup()
    
    yield manager
    
    manager.teardown()


@pytest.fixture(scope="session")
def session_db(db_manager):
    """Get the raw database connection (session-scoped)."""
    return db_manager.db


@pytest.fixture(scope="session")
def modules(session_db):
    """
    Get the module registry (session-scoped).
    
    Modules are lazy-loaded on first access.
    """
    return ModuleRegistry(session_db)


@pytest.fixture(scope="session")
def session_users(modules):
    """
    Pre-create a pool of users at session start.
    
    This takes ~5-10 seconds with real Argon2 hashing, but then
    ALL tests can reuse these users without any hashing overhead.
    
    Returns a list of (user, username, password) tuples.
    """
    users = []
    print("\n[Setup] Creating user pool with real Argon2 hashing...")
    
    for i in range(100):  # Create 100 users upfront
        username = f"pooluser_{i}_{uuid.uuid4().hex[:4]}"
        email = f"{username}@test.example.com"
        password = TEST_PASSWORD
        
        user = modules.auth.register(
            username=username,
            email=email,
            password=password
        )
        users.append((user, username, password))
    
    print(f"[Setup] Created {len(users)} users in pool")
    return users


# =============================================================================
# User Pool Management
# =============================================================================

class UserPool:
    """Manages a pool of pre-created users for test reuse."""
    
    def __init__(self, users, auth_module):
        self._users = users  # List of (user, username, password) tuples
        self._index = 0
        self._auth = auth_module
    
    def get_user(self):
        """Get the next user from the pool."""
        if self._index >= len(self._users):
            raise RuntimeError(
                f"User pool exhausted! Only {len(self._users)} users available. "
                "Consider increasing pool size or reusing users better."
            )
        user, username, password = self._users[self._index]
        self._index += 1
        return user
    
    def get_user_with_credentials(self):
        """Get user with username and password."""
        if self._index >= len(self._users):
            raise RuntimeError("User pool exhausted!")
        user, username, password = self._users[self._index]
        self._index += 1
        return user, username, password
    
    def get_user_with_token(self):
        """Get user and log them in."""
        user, username, password = self.get_user_with_credentials()
        result = self._auth.login(username, password)
        return user, result.token
    
    def reset(self):
        """Reset pool index for next test."""
        self._index = 0
    
    @property
    def remaining(self):
        """Number of users remaining in pool."""
        return len(self._users) - self._index


@pytest.fixture
def user_pool(modules, session_users):
    """
    Get a user pool for the current test.
    
    Pool is reset at the start of each test so tests get fresh users.
    """
    pool = UserPool(session_users, modules.auth)
    return pool


# =============================================================================
# Convenience Fixtures
# =============================================================================

@pytest.fixture
def test_user(user_pool):
    """Get a single test user from the pool."""
    return user_pool.get_user()


@pytest.fixture
def test_user_with_token(user_pool):
    """Get a test user and log them in."""
    return user_pool.get_user_with_token()


@pytest.fixture
def two_users(user_pool):
    """Get two test users from the pool."""
    return user_pool.get_user(), user_pool.get_user()


@pytest.fixture
def three_users(user_pool):
    """Get three test users from the pool."""
    return user_pool.get_user(), user_pool.get_user(), user_pool.get_user()


# =============================================================================
# Legacy Compatibility Fixtures
# =============================================================================

@pytest.fixture
def db_and_auth(modules):
    """Legacy fixture - returns (db, auth) tuple."""
    return modules._db, modules.auth


@pytest.fixture
def db_and_modules(modules):
    """Legacy fixture - returns tuple of common modules."""
    return (
        modules._db,
        modules.auth,
        modules.messaging,
        modules.servers,
        modules.relationships,
        modules.presence,
    )


@pytest.fixture
def registered_user(user_pool, modules):
    """
    Legacy fixture - returns (user, auth, username) tuple.
    
    Uses pool user for speed. For tests that need truly fresh users
    (like testing registration), use modules.auth.register() directly.
    """
    user, username, password = user_pool.get_user_with_credentials()
    return user, modules.auth, username


@pytest.fixture
def logged_in_user(user_pool, modules):
    """
    Legacy fixture - returns (user, token, auth, username) tuple.
    
    Uses pool user for speed.
    """
    user, username, password = user_pool.get_user_with_credentials()
    result = modules.auth.login(username, password)
    return user, result.token, modules.auth, username


# =============================================================================
# Server/Messaging Fixtures
# =============================================================================

@pytest.fixture
def test_server(modules, user_pool):
    """Create a test server with owner from pool."""
    owner = user_pool.get_user()
    server = modules.servers.create_server(
        owner_id=owner.id,
        name=f"Test Server {uuid.uuid4().hex[:6]}"
    )
    return server, owner


@pytest.fixture
def test_server_with_members(modules, user_pool):
    """Create a test server with owner and 2 members."""
    owner = user_pool.get_user()
    member1 = user_pool.get_user()
    member2 = user_pool.get_user()
    
    server = modules.servers.create_server(
        owner_id=owner.id,
        name=f"Test Server {uuid.uuid4().hex[:6]}"
    )
    
    modules.servers.add_member(server.id, member1.id)
    modules.servers.add_member(server.id, member2.id)
    
    return server, owner, [member1, member2]


@pytest.fixture
def test_dm(modules, user_pool):
    """Create a DM conversation between two pool users."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()
    dm = modules.messaging.create_dm(user1.id, user2.id)
    return dm, user1, user2


@pytest.fixture
def test_group(modules, user_pool):
    """Create a group conversation."""
    owner = user_pool.get_user()
    member1 = user_pool.get_user()
    member2 = user_pool.get_user()
    
    group = modules.messaging.create_group(
        owner_id=owner.id,
        name=f"Test Group {uuid.uuid4().hex[:6]}",
        participant_ids=[member1.id, member2.id]
    )
    return group, owner, [member1, member2]


# =============================================================================
# API Testing Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def api_module(modules):
    """Get the API module with all dependencies."""
    return modules.get_api()


@pytest.fixture(scope="session")
def test_client(api_module):
    """Create a FastAPI test client."""
    from fastapi.testclient import TestClient
    from src.api import create_app
    
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers(test_user_with_token):
    """Get authorization headers for authenticated requests."""
    user, token = test_user_with_token
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# Test Configuration
# =============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Fast unit tests, no database required")
    config.addinivalue_line("markers", "integration: Tests requiring full module setup")
    config.addinivalue_line("markers", "slow: Intentionally slow tests (rate limiting, timeouts)")
    config.addinivalue_line("markers", "auth: Authentication module tests")
    config.addinivalue_line("markers", "messaging: Messaging module tests")
    config.addinivalue_line("markers", "servers: Server module tests")
    config.addinivalue_line("markers", "presence: Presence module tests")
    config.addinivalue_line("markers", "relationships: Relationships module tests")
    config.addinivalue_line("markers", "reactions: Reactions module tests")
    config.addinivalue_line("markers", "webhooks: Webhooks module tests")
    config.addinivalue_line("markers", "threads: Threads module tests")
    config.addinivalue_line("markers", "notifications: Notifications module tests")
    config.addinivalue_line("markers", "ratelimit: Rate limiting tests")
    config.addinivalue_line("markers", "api: API route tests")
    config.addinivalue_line("markers", "media: Media module tests")
    config.addinivalue_line("markers", "search: Search module tests")
    config.addinivalue_line("markers", "applications: Applications module tests")
    config.addinivalue_line("markers", "stickers: Stickers module tests")
    config.addinivalue_line("markers", "polls: Polls module tests")
    config.addinivalue_line("markers", "soundboard: Soundboard module tests")


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests based on their location."""
    for item in items:
        # Add markers based on test file path
        if "/auth/" in str(item.fspath):
            item.add_marker(pytest.mark.auth)
            item.add_marker(pytest.mark.integration)
        elif "/messaging/" in str(item.fspath):
            item.add_marker(pytest.mark.messaging)
            item.add_marker(pytest.mark.integration)
        elif "/servers/" in str(item.fspath):
            item.add_marker(pytest.mark.servers)
            item.add_marker(pytest.mark.integration)
        elif "/presence/" in str(item.fspath):
            item.add_marker(pytest.mark.presence)
            item.add_marker(pytest.mark.integration)
        elif "/relationships/" in str(item.fspath):
            item.add_marker(pytest.mark.relationships)
            item.add_marker(pytest.mark.integration)
        elif "/reactions/" in str(item.fspath):
            item.add_marker(pytest.mark.reactions)
            item.add_marker(pytest.mark.integration)
        elif "/webhooks/" in str(item.fspath):
            item.add_marker(pytest.mark.webhooks)
            item.add_marker(pytest.mark.integration)
        elif "/threads/" in str(item.fspath):
            item.add_marker(pytest.mark.threads)
            item.add_marker(pytest.mark.integration)
        elif "/notifications/" in str(item.fspath):
            item.add_marker(pytest.mark.notifications)
            item.add_marker(pytest.mark.integration)
        elif "/ratelimit/" in str(item.fspath):
            item.add_marker(pytest.mark.ratelimit)
            item.add_marker(pytest.mark.integration)
        elif "/api/" in str(item.fspath):
            item.add_marker(pytest.mark.api)
            item.add_marker(pytest.mark.integration)
        elif "/media/" in str(item.fspath):
            item.add_marker(pytest.mark.media)
            item.add_marker(pytest.mark.integration)
        elif "/applications/" in str(item.fspath):
            item.add_marker(pytest.mark.applications)
            item.add_marker(pytest.mark.integration)
        elif "/stickers/" in str(item.fspath):
            item.add_marker(pytest.mark.stickers)
            item.add_marker(pytest.mark.integration)
        elif "/polls/" in str(item.fspath):
            item.add_marker(pytest.mark.polls)
            item.add_marker(pytest.mark.integration)
        elif "/soundboard/" in str(item.fspath):
            item.add_marker(pytest.mark.soundboard)
            item.add_marker(pytest.mark.integration)
        elif "/unit/" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
