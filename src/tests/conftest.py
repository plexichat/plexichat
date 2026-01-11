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

from src.utils import encryption

# Load custom pytest plugins
pytest_plugins = ["src.tests.pytest_plugins"]

# Ensure src is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, utils_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

from unittest.mock import Mock  # noqa: E402
from src.tests.fixtures.config import TEST_PASSWORD  # noqa: E402
from src.tests.fixtures.database import DatabaseManager  # noqa: E402
from src.tests.fixtures.modules import ModuleRegistry  # noqa: E402
from src.tests.fixtures.factories import (  # noqa: E402
    UserFactory,
    ServerFactory,
    ConversationFactory,
)
from hypothesis import settings, HealthCheck  # noqa: E402

# Register a global profile for CI/Tests
settings.register_profile(
    "ci",
    suppress_health_check=[
        HealthCheck.filter_too_much,
        HealthCheck.data_too_large,
        HealthCheck.too_slow,
    ],
    deadline=None,
)
settings.load_profile("ci")


@pytest.fixture(scope="session", autouse=True)
def setup_config():
    """Initialize global configuration for tests."""
    import utils.config as config
    import utils.version as version

    config_dir = "temp_test_config"
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "config.yaml")

    # Force clean config for each session
    if os.path.exists(config_path):
        try:
            os.remove(config_path)
        except OSError:
            pass

    # Minimal default config for tests
    default_config = {
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
                "max_per_user": 20,
                "extend_on_activity": True,
                "extend_threshold_hours": 24,
            },
            "totp": {"backup_code_count": 10, "issuer": "TestApp"},
            "security": {
                "max_failed_attempts": 100,
                "lockout_duration_minutes": 1,
            },
            "password": {
                "min_length": 12,
                "max_length": 128,
                "require_uppercase": True,
                "require_lowercase": True,
                "require_digit": True,
                "require_special": True,
            },
        },
        "messaging": {
            "max_message_length": 4000,
            "max_attachments": 10,
            "max_attachments_per_message": 10,
        },
        "media": {"compression": {"enabled": False}},
        "applications": {"max_applications_per_user": 1000},
        "ratelimit": {
            "enabled": True,
            "global": {"limit": 100, "window": 60},
            "user": {"limit": 5, "window": 60},
            "ip": {"limit": 10, "window": 60},
        },
        "api": {
            "cors_origins": ["http://testserver", "http://localhost:3000"],
            "allow_wildcard_cors": True,
            "cors_allow_headers": [
                "Authorization",
                "Content-Type",
                "X-Requested-With",
                "Accept",
                "Origin",
                "X-Custom-Header",
            ],
        },
        "version": {"current": "r.1.0-1", "min_client": "a.1.0-1"},
        "servers": {"templates": {"max_templates_per_user": 1000}},
    }

    config.setup(config_path=config_path, default_config=default_config)
    version.setup(current_version="r.1.0-1", min_supported_version="a.1.0-1")

    # Initialize logger for tests
    import utils.logger as logger

    log_dir = "temp_test_logs"
    os.makedirs(log_dir, exist_ok=True)
    try:
        logger.setup(log_dir=log_dir, level="DEBUG", zip_logs=False)
    except Exception:
        pass

    yield
    # Cleanup
    if os.path.exists(config_path):
        try:
            os.remove(config_path)
            os.rmdir(config_dir)
        except OSError:
            pass


# =============================================================================
# Session-Scoped Fixtures (initialized once per test run)
# =============================================================================


@pytest.fixture(scope="session")
def db_manager():
    """
    Create and manage the test database for the entire session.

    This is the key optimization - ONE database for all tests.
    """
    # Use xdist worker ID to ensure unique snowflake IDs across parallel workers
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    worker_num = 1
    if worker_id and worker_id.startswith("gw"):
        try:
            worker_num = int(worker_id[2:]) + 1
        except ValueError:
            worker_num = 1

    encryption.setup(
        worker_id=worker_num,
        argon2_time_cost=1,
        argon2_memory_cost=8192,
        argon2_parallelism=1,
    )
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

    # Increased for performance/integration tests that consume many pooled users
    for i in range(200):
        username = f"pooluser_{i}_{uuid.uuid4().hex[:4]}"
        email = f"{username}@test.example.com"
        password = TEST_PASSWORD

        user = modules.auth.register(username=username, email=email, password=password)
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
# Factory Fixtures
# =============================================================================


@pytest.fixture
def user_factory(modules, session_users):
    """Get a user factory for creating test users."""
    factory = UserFactory(auth_module=modules.auth)
    factory._pool = [user for user, _, _ in session_users]
    return factory


@pytest.fixture
def server_factory(modules, user_factory):
    """Get a server factory for creating test servers."""
    return ServerFactory(servers_module=modules.servers, user_factory=user_factory)


@pytest.fixture
def conversation_factory(modules, user_factory):
    """Get a conversation factory for creating test conversations."""
    return ConversationFactory(
        messaging_module=modules.messaging, user_factory=user_factory
    )


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


@pytest.fixture
def db(postgres_db):
    """Alias for postgres_db fixture to support tests expecting 'db' parameter."""
    return postgres_db


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
        owner_id=owner.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
    )
    return server, owner


@pytest.fixture
def test_server_with_members(modules, user_pool):
    """Create a test server with owner and 2 members."""
    owner = user_pool.get_user()
    member1 = user_pool.get_user()
    member2 = user_pool.get_user()

    server = modules.servers.create_server(
        owner_id=owner.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
    )

    modules.servers.add_member(server.id, member1.id)
    modules.servers.add_member(server.id, member2.id)

    return server, owner, [member1, member2]


# =============================================================================
# Comprehensive Test Fixtures (for manager tests)
# =============================================================================


@pytest.fixture
def test_db():
    """Create a fresh in-memory database for each test."""
    import utils.config as config
    from src.core.database import Database
    from src.core.auth.schema import create_tables as create_auth_tables
    from src.core.messaging.schema import create_tables as create_messaging_tables
    from src.core.servers.schema import create_tables as create_server_tables
    from src.core.relationships.schema import (
        create_tables as create_relationship_tables,
    )
    from src.core.reactions.schema import create_tables as create_reaction_tables
    from src.core.media.schema import create_tables as create_media_tables
    from src.core.presence.schema import create_tables as create_presence_tables
    from src.core.webhooks.schema import create_tables as create_webhook_tables
    from src.core.threads.schema import create_tables as create_thread_tables
    from src.core.notifications.schema import (
        create_tables as create_notification_tables,
    )
    from src.core.polls.schema import create_tables as create_polls_tables

    # Save current config to restore later
    old_db_conf = config.get("database", None)
    config.set("database", {"type": "sqlite", "path": ":memory:"})

    db = Database()
    db.connect()

    # Critical: Create auth tables first because other modules have foreign keys to them
    # Use unique worker_id for parallel tests
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    worker_num = 1
    if worker_id and worker_id.startswith("gw"):
        try:
            worker_num = int(worker_id[2:]) + 1
        except ValueError:
            worker_num = 1
    encryption.setup(
        worker_id=worker_num,
        argon2_time_cost=1,
        argon2_memory_cost=8192,
        argon2_parallelism=1,
    )

    create_auth_tables(db)
    create_messaging_tables(db)
    create_server_tables(db)
    create_relationship_tables(db)
    create_reaction_tables(db)
    create_media_tables(db)
    create_presence_tables(db)
    create_webhook_tables(db)
    create_thread_tables(db)
    create_notification_tables(db)
    create_polls_tables(db)

    # Insert system user (ID 0) for system messages
    db.execute(
        "INSERT INTO auth_users (id, account_type, username, email, password_hash, permissions, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (0, "system", "system", "system@localhost", "!", "{}", 0, 0),
    )

    # Insert default users for tests that assume fixed IDs (like manager tests)
    for i in range(1, 11):
        db.execute(
            "INSERT INTO auth_users (id, account_type, username, email, password_hash, permissions, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                i,
                "user",
                f"testuser{i}",
                f"fixture_user{i}@example.com",
                "fake_hash",
                "{}",
                0,
                0,
            ),
        )

    yield db

    # Restore config
    if old_db_conf:
        config.set("database", old_db_conf)
    db.close()


@pytest.fixture
def auth_manager(test_db):
    """AuthManager fixture."""
    from src.core.auth.manager import AuthManager

    return AuthManager(test_db)


@pytest.fixture
def email_sender():
    """Mock email sender."""
    mock = Mock()
    mock.send = Mock(return_value=True)
    return mock


@pytest.fixture
def messaging_manager(test_db):
    """MessagingManager fixture."""
    from src.core.messaging.manager import MessagingManager

    return MessagingManager(test_db)


@pytest.fixture
def server_manager(test_db):
    """ServerManager fixture."""
    from src.core.servers.manager import ServerManager

    return ServerManager(test_db)


@pytest.fixture
def presence_manager(test_db):
    """PresenceManager fixture."""
    from src.core.presence.manager import PresenceManager

    return PresenceManager(test_db)


@pytest.fixture
def rel_manager(test_db):
    """RelationshipManager fixture."""
    from src.core.relationships.manager import RelationshipManager

    return RelationshipManager(test_db)


@pytest.fixture
def reaction_manager(test_db):
    """ReactionManager fixture."""
    from src.core.reactions.manager import ReactionManager

    return ReactionManager(test_db)


@pytest.fixture
def webhook_manager(test_db):
    """WebhookManager fixture."""
    from src.core.webhooks.manager import WebhookManager

    return WebhookManager(test_db)


@pytest.fixture
def thread_manager(test_db):
    """ThreadManager fixture."""
    from src.core.threads.manager import ThreadManager

    return ThreadManager(test_db)


@pytest.fixture
def notification_manager(test_db):
    """NotificationManager fixture."""
    from src.core.notifications.manager import NotificationManager

    return NotificationManager(test_db)


@pytest.fixture
def media_manager(test_db):
    """MediaManager fixture."""
    from src.core.media.manager import MediaManager

    return MediaManager(test_db)


@pytest.fixture
def search_manager(test_db):
    """SearchManager fixture."""
    from src.core.search.manager import SearchManager

    return SearchManager(test_db)


@pytest.fixture
def app_manager(test_db):
    """ApplicationManager fixture."""
    from src.core.applications.manager import ApplicationManager

    return ApplicationManager(test_db)


@pytest.fixture
def sticker_manager(test_db):
    """StickerManager fixture."""
    from src.core.stickers.manager import StickerManager

    return StickerManager(test_db)


@pytest.fixture
def poll_manager(test_db):
    """PollManager fixture."""
    from src.core.polls.manager import PollManager

    return PollManager(test_db)


@pytest.fixture
def soundboard_manager(test_db):
    """SoundboardManager fixture."""
    from src.core.soundboard.manager import SoundboardManager

    return SoundboardManager(test_db)


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
        participant_ids=[member1.id, member2.id],
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
    """Create a FastAPI test client with rate limiting disabled by default."""
    from fastapi.testclient import TestClient
    from src.api import create_app

    # Disable rate limiting for general functional tests
    app = create_app(enable_rate_limiting=False)
    return TestClient(app)


@pytest.fixture(scope="session")
def rate_limit_client(api_module):
    """Create a FastAPI test client with rate limiting enabled."""
    from fastapi.testclient import TestClient
    from src.api import create_app

    # Enable rate limiting specifically for rate limit tests
    app = create_app(enable_rate_limiting=True)
    return TestClient(app)


@pytest.fixture
def auth_headers(test_user_with_token):
    """Get authorization headers for authenticated requests."""
    user, token = test_user_with_token
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# Production Simulation Fixtures
# =============================================================================


@pytest.fixture
def multiprocess_config(postgres_config):
    """Configuration for multi-process testing with shared PostgreSQL pool settings."""
    return {
        **postgres_config,
        'pool_size': 20,
        'max_overflow': 10,
        'pool_timeout': 30,
        'pool_recycle': 3600,
    }


@pytest.fixture
def worker_pool(multiprocess_config):
    """Creates a pool of worker processes that share the same PostgreSQL connection pool configuration."""
    from src.tests.test_production_simulation import ProductionSimulator
    
    simulator = ProductionSimulator(
        db_config=multiprocess_config,
        worker_count=4,
        queries_per_worker=50
    )
    yield simulator
    # Cleanup
    simulator.terminate_all_workers()
    simulator.join_all_workers(timeout=10)


@pytest.fixture
def redis_with_postgres(postgres_config):
    """Sets up both Redis and PostgreSQL for integrated testing."""
    import fakeredis
    
    fake_redis = fakeredis.FakeRedis()
    
    # Return both Redis and PostgreSQL config for integrated testing
    return {
        'redis': fake_redis,
        'postgres_config': postgres_config,
    }


# =============================================================================
# Test Configuration
# =============================================================================


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Fast unit tests, no database required")
    config.addinivalue_line("markers", "integration: Tests requiring full module setup")
    config.addinivalue_line(
        "markers", "slow: Intentionally slow tests (rate limiting, timeouts)"
    )
    config.addinivalue_line(
        "markers", "security: Security-critical test that must not fail"
    )
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
    config.addinivalue_line("markers", "settings: User settings module tests")
    config.addinivalue_line("markers", "voice: Voice module tests")
    config.addinivalue_line("markers", "websocket: WebSocket module tests")
    config.addinivalue_line("markers", "encryption: Encryption module tests")
    config.addinivalue_line("markers", "embeds: Embeds module tests")
    config.addinivalue_line("markers", "automod: Auto-moderation module tests")
    config.addinivalue_line("markers", "performance: Performance and load tests")
    config.addinivalue_line("markers", "production_simulation: Production environment simulation tests")
    config.addinivalue_line("markers", "multiprocess: Tests using multiple processes")
    config.addinivalue_line("markers", "requires_postgres: Tests requiring PostgreSQL Docker container")


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests based on their location."""
    for item in items:
        # Add markers based on test file path
        # Normalize path to use forward slashes for cross-platform compatibility
        item_path = str(item.fspath).replace(os.sep, "/")

        # Security tests (mark as critical)
        if "/security/" in item_path or "test_security" in item.name.lower():
            item.add_marker(pytest.mark.security)

        if "/auth/" in item_path:
            item.add_marker(pytest.mark.auth)
            item.add_marker(pytest.mark.integration)
        elif "/messaging/" in item_path:
            item.add_marker(pytest.mark.messaging)
            item.add_marker(pytest.mark.integration)
        elif "/servers/" in item_path:
            item.add_marker(pytest.mark.servers)
            item.add_marker(pytest.mark.integration)
        elif "/presence/" in item_path:
            item.add_marker(pytest.mark.presence)
            item.add_marker(pytest.mark.integration)
        elif "/relationships/" in item_path:
            item.add_marker(pytest.mark.relationships)
            item.add_marker(pytest.mark.integration)
        elif "/reactions/" in item_path:
            item.add_marker(pytest.mark.reactions)
            item.add_marker(pytest.mark.integration)
        elif "/webhooks/" in item_path:
            item.add_marker(pytest.mark.webhooks)
            item.add_marker(pytest.mark.integration)
        elif "/threads/" in item_path:
            item.add_marker(pytest.mark.threads)
            item.add_marker(pytest.mark.integration)
        elif "/notifications/" in item_path:
            item.add_marker(pytest.mark.notifications)
            item.add_marker(pytest.mark.integration)
        elif "/ratelimit/" in item_path:
            item.add_marker(pytest.mark.ratelimit)
            item.add_marker(pytest.mark.integration)
        elif "/api/" in item_path:
            item.add_marker(pytest.mark.api)
            item.add_marker(pytest.mark.integration)
        elif "/media/" in item_path:
            item.add_marker(pytest.mark.media)
            item.add_marker(pytest.mark.integration)
        elif "/applications/" in item_path:
            item.add_marker(pytest.mark.applications)
            item.add_marker(pytest.mark.integration)
        elif "/stickers/" in item_path:
            item.add_marker(pytest.mark.stickers)
            item.add_marker(pytest.mark.integration)
        elif "/polls/" in item_path:
            item.add_marker(pytest.mark.polls)
            item.add_marker(pytest.mark.integration)
        elif "/soundboard/" in item_path:
            item.add_marker(pytest.mark.soundboard)
            item.add_marker(pytest.mark.integration)
        elif "/settings/" in item_path:
            item.add_marker(pytest.mark.settings)
            item.add_marker(pytest.mark.integration)
        elif "/voice/" in item_path:
            item.add_marker(pytest.mark.voice)
            item.add_marker(pytest.mark.integration)
        elif "/websocket/" in item_path:
            item.add_marker(pytest.mark.websocket)
            item.add_marker(pytest.mark.integration)
        elif "/encryption/" in item_path:
            item.add_marker(pytest.mark.encryption)
            item.add_marker(pytest.mark.integration)
        elif "/embeds/" in item_path:
            item.add_marker(pytest.mark.embeds)
            item.add_marker(pytest.mark.integration)
        elif "/automod/" in item_path:
            item.add_marker(pytest.mark.automod)
            item.add_marker(pytest.mark.integration)
        elif "/performance/" in item_path:
            item.add_marker(pytest.mark.performance)
            item.add_marker(pytest.mark.slow)
        elif "/unit/" in item_path:
            item.add_marker(pytest.mark.unit)
