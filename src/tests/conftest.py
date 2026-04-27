"""
Root conftest.py - Simple, fast fixtures for all tests.

PRINCIPLES:
- Function-scoped databases for complete test isolation
- Fake hashing for instant test execution
- Simple, clear fixture dependencies
- No complex user pools or session-scoped state
- No legacy compatibility layers
"""

import pytest
import os
import sys
import uuid
import tempfile
from unittest.mock import Mock, patch

# Import encryption at module level for fixtures
from src.utils import encryption

# Setup paths at import time
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, utils_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

import utils.config as config  # noqa: E402
import utils.version as version  # noqa: E402

# =============================================================================
# Test Configuration
# =============================================================================

DEFAULT_TEST_CONFIG = {
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
        "totp": {"backup_code_count": 10, "issuer": "TestApp"},
        "security": {
            "max_failed_attempts": 3,
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
    "rate_limiting": {
        "enabled": True,
        "admin_bypass": True,
        "internal_bypass": True,
        "global": {"requests": 50, "window_seconds": 1.0, "burst": 10},
        "user": {
            "requests": 70,
            "window_seconds": 60.0,
            "burst": 20,
            "hourly_limit": 3600,
            "daily_limit": 50000,
        },
        "ip": {
            "requests": 70,
            "window_seconds": 60.0,
            "burst": 10,
            "hourly_limit": 1800,
            "daily_limit": 10000,
        },
        "routes": {"send_message": {"requests": 5, "window": 5.0}},
        "bot_multiplier": 1.5,
        "webhook_multiplier": 1.0,
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

# =============================================================================
# Setup Config at Import Time (Before Any Test Collection)
# =============================================================================

# Setup config immediately at import time to avoid import errors
_test_config_dir = tempfile.mkdtemp()
_test_config_path = os.path.join(_test_config_dir, "config.yaml")
config.setup(config_path=_test_config_path, default_config=DEFAULT_TEST_CONFIG)
version.setup(current_version="r.1.0-1", min_supported_version="a.1.0-1")

# Initialize logger for tests
import utils.logger as logger

_test_log_dir = tempfile.mkdtemp()
try:
    logger.setup(log_dir=_test_log_dir, level="WARNING", zip_logs=False)
except Exception:
    pass

# Patch Database to add missing fetch_last_insert_id method for migration tracker
from src.core.database import Database

if not hasattr(Database, "fetch_last_insert_id"):

    def fetch_last_insert_id(self):
        """Get the last insert ID (compatibility shim for migration tracker)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT last_insert_rowid()")
        result = cursor.fetchone()
        return result[0] if result else None

    Database.fetch_last_insert_id = fetch_last_insert_id


@pytest.fixture(scope="session", autouse=True)
def setup_config(tmpdir_factory):
    """Initialize global configuration for tests (already done at import time)."""
    # Config is already setup at import time above
    # This fixture exists for backward compatibility
    yield


# =============================================================================
# Database Fixture (Function-Scoped for Isolation)
# =============================================================================


@pytest.fixture
def db(setup_config):
    """
    Create a clean database for each test.

    Uses a fresh database file for each test for complete isolation.
    """
    from src.core.database import Database
    from src.core.migrations import run_migrations
    from src.utils import encryption

    # Create a temporary database file for this test
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db.close()

    # Save current config to restore later
    old_db_conf = config.get("database", None)
    config.set("database", {"type": "sqlite", "path": temp_db.name})

    db = Database()
    db.connect()

    # Use fake hashing for fast tests
    encryption.setup(
        worker_id=1,
        argon2_time_cost=1,
        argon2_memory_cost=8,
        argon2_parallelism=1,
    )

    # Mock the hashing to be instant
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        with patch.object(encryption, "verify_password", return_value=True):
            # Skip migration 028 (irreversible, high-risk) for tests
            from src.core.migrations import manager as migration_manager

            def skip_028_apply_all(self, dry_run=False):
                # Get list of pending migrations
                pending = self.get_pending_migrations()
                # Filter out migration 028
                filtered = [m for m in pending if m.version != "028"]
                # Apply filtered migrations
                results = {
                    "success": True,
                    "applied_count": 0,
                    "failed_count": 0,
                    "migrations": [],
                    "dry_run": dry_run,
                }
                for migration in filtered:
                    result = self._execute_migration(migration, dry_run)
                    results["migrations"].append(result)
                    results["applied_count"] += 1
                return results

            with patch.object(
                migration_manager.MigrationManager,
                "apply_all_pending",
                skip_028_apply_all,
            ):
                run_migrations(db)

            # Insert system user (ID 0) for system messages
            db.execute(
                "INSERT INTO auth_users (id, account_type, username, password_hash, permissions, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (0, "system", "system", "!", "{}", 0, 0),
            )

            # Setup reports and feedback tables
            try:
                from src.core import reports, feedback

                reports.setup(db)
                feedback.setup(db)
            except Exception:
                # If reports/feedback setup fails, create minimal tables manually
                pass  # Tables will be created by migrations or not needed

            # Ensure required tables exist (workarounds for migration issues)
            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL,
                    device_id INTEGER,
                    ip_index TEXT,
                    ip_encrypted TEXT,
                    user_agent TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_activity INTEGER NOT NULL,
                    revoked INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
                )
            """)

            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_api_access_tokens (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    token_index TEXT UNIQUE NOT NULL,
                    token_encrypted TEXT NOT NULL,
                    created_by INTEGER,
                    created_at INTEGER NOT NULL,
                    first_used_at INTEGER,
                    last_used_at INTEGER,
                    last_used_ip_index TEXT,
                    last_used_ip_encrypted TEXT,
                    last_used_user_agent TEXT,
                    last_used_path TEXT,
                    expires_at INTEGER,
                    scope_mode TEXT NOT NULL DEFAULT 'none',
                    use_count_total INTEGER NOT NULL DEFAULT 0,
                    revoked INTEGER DEFAULT 0,
                    revoked_at INTEGER,
                    revoked_by INTEGER
                )
            """)

            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_ip_blacklist (
                    ip_index TEXT PRIMARY KEY,
                    ip_encrypted TEXT,
                    reason TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER
                )
            """)

            db.execute("""
                CREATE TABLE IF NOT EXISTS srv_servers (
                    id INTEGER PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    icon TEXT,
                    banner TEXT,
                    invite_code TEXT UNIQUE,
                    region TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    member_count INTEGER DEFAULT 0,
                    max_members INTEGER DEFAULT 100,
                    is_nsfw INTEGER DEFAULT 0,
                    is_public INTEGER DEFAULT 1,
                    verification_level INTEGER DEFAULT 0,
                    default_channel_id INTEGER,
                    FOREIGN KEY (owner_id) REFERENCES auth_users(id) ON DELETE CASCADE
                )
            """)

            yield db

    # Cleanup
    try:
        db.close()
    except:
        pass

    try:
        os.unlink(temp_db.name)
    except:
        pass

    # Restore config
    if old_db_conf:
        config.set("database", old_db_conf)


@pytest.fixture
def test_db(db):
    """Alias for db fixture for backward compatibility."""
    return db


# =============================================================================
# Module Fixtures (Function-Scoped)
# =============================================================================


@pytest.fixture
def auth_manager(db):
    """AuthManager fixture."""
    from src.core import auth

    # Set up the global auth module so API middleware can use it
    auth.setup(db)

    # Return the global auth module (which contains the AuthManager)
    return auth


@pytest.fixture
def messaging_manager(db):
    """MessagingManager fixture."""
    from src.core.messaging.manager import MessagingManager

    return MessagingManager(db)


@pytest.fixture
def server_manager(db):
    """ServerManager fixture."""
    from src.core.servers.manager import ServerManager

    manager = ServerManager(db)
    # Disable encryption for tests to avoid keyring issues
    manager._encrypt_descriptions = False
    return manager


@pytest.fixture
def rel_manager(db):
    """RelationshipManager fixture."""
    from src.core.relationships.manager import RelationshipManager

    return RelationshipManager(db)


@pytest.fixture
def presence_manager(db, rel_manager):
    """PresenceManager fixture."""
    from src.core.presence.manager import PresenceManager

    return PresenceManager(db, relationships_module=rel_manager)


@pytest.fixture
def reaction_manager(db):
    """ReactionManager fixture."""
    from src.core.reactions.manager import ReactionManager

    return ReactionManager(db)


@pytest.fixture
def webhook_manager(db):
    """WebhookManager fixture."""
    from src.core.webhooks.manager import WebhookManager

    return WebhookManager(db)


@pytest.fixture
def thread_manager(db):
    """ThreadManager fixture."""
    from src.core.threads.manager import ThreadManager

    return ThreadManager(db)


@pytest.fixture
def notification_manager(db):
    """NotificationManager fixture."""
    from src.core.notifications.manager import NotificationManager

    return NotificationManager(db)


@pytest.fixture
def automod_manager(db, server_manager, messaging_manager, notification_manager):
    """AutoModManager fixture."""
    from src.core.automod.manager import AutoModManager

    return AutoModManager(db, server_manager, messaging_manager, notification_manager)


@pytest.fixture
def media_manager(db):
    """MediaManager fixture."""
    from src.core.media.manager import MediaManager

    return MediaManager(db)


@pytest.fixture
def search_manager(db, auth_manager, messaging_manager, server_manager):
    """SearchManager fixture."""
    from src.core.search.manager import SearchManager

    return SearchManager(db, auth_manager, messaging_manager, server_manager)


@pytest.fixture
def app_manager(db):
    """ApplicationManager fixture."""
    from src.core.applications.manager import ApplicationManager

    return ApplicationManager(db)


@pytest.fixture
def sticker_manager(db):
    """StickerManager fixture."""
    from src.core.stickers.manager import StickerManager

    return StickerManager(db)


@pytest.fixture
def poll_manager(db):
    """PollManager fixture."""
    from src.core.polls.manager import PollManager

    return PollManager(db)


@pytest.fixture
def soundboard_manager(db):
    """SoundboardManager fixture."""
    from src.core.soundboard.manager import SoundboardManager

    return SoundboardManager(db)


@pytest.fixture
def embeds_manager(db, messaging_manager, server_manager):
    """EmbedsManager fixture."""
    from src.core import embeds

    embeds.setup(db, messaging_manager, server_manager)
    return embeds._manager


@pytest.fixture
def events_module(rel_manager, server_manager, messaging_manager):
    """Events module fixture."""
    from src.core import events

    events.setup(rel_manager, server_manager, messaging_manager)
    return events


@pytest.fixture
def reports(db):
    """Reports module fixture (setup already done in db fixture)."""
    from src.core import reports

    return reports


@pytest.fixture
def feedback(db):
    """Feedback module fixture (setup already done in db fixture)."""
    from src.core import feedback

    return feedback


# =============================================================================
# Helper Fixtures for Creating Test Data
# =============================================================================


@pytest.fixture
def test_user(auth_manager):
    """Create a test user with fake hashing."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=f"test_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
    return user


@pytest.fixture
def registered_user(auth_manager):
    """Create a registered user for tests (returns tuple: user, auth_manager, username)."""
    username = f"testuser_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    return user, auth_manager, username


@pytest.fixture
def logged_in_user(auth_manager):
    """Create a logged-in user for tests (returns tuple: user, token, auth_manager, username)."""
    username = f"testuser_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return user, result.token, auth_manager, username


@pytest.fixture
def test_user_with_token(auth_manager, test_user):
    """Create a test user and return them with their auth token (dict format for compatibility)."""
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(test_user.username, "TestPass123!")
    return {
        "user": test_user,
        "token": result.token,
        "username": test_user.username,
        "password": "TestPass123!",
    }


@pytest.fixture
def two_users(auth_manager):
    """Create two test users."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=f"test1_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=f"test2_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
    return user1, user2


@pytest.fixture
def three_users(auth_manager):
    """Create three test users."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=f"test1_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=f"test2_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
        user3 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=f"test3_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
    return user1, user2, user3


@pytest.fixture
def test_server(server_manager, test_user):
    """Create a test server."""
    server = server_manager.create_server(
        owner_id=test_user.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
    )
    return server, test_user


@pytest.fixture
def test_dm(messaging_manager, two_users):
    """Create a DM conversation between two users."""
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    return dm, user1, user2


@pytest.fixture
def fresh_users_with_dm(auth_manager, messaging_manager, embeds_manager):
    """Create two fresh users with a DM and a message for embeds testing."""
    # Create two users with fake hashing
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username=f"embed_user1_{uuid.uuid4().hex[:8]}",
            email=f"embed1_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"embed_user2_{uuid.uuid4().hex[:8]}",
            email=f"embed2_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

    # Create DM
    dm = messaging_manager.create_dm(user1.id, user2.id)

    # Send a message
    msg = messaging_manager.send_message(user1.id, dm.id, "Test message")

    # Return tuple: (user1, user2, dm, msg, embeds, messaging)
    return user1, user2, dm, msg, embeds_manager, messaging_manager


@pytest.fixture
def users_with_dm(messaging_manager, search_manager, two_users):
    """Create two users with a DM and messages for search testing."""
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    messages = [
        messaging_manager.send_message(user1.id, dm.id, "hello world"),
        messaging_manager.send_message(user2.id, dm.id, "hi there"),
    ]
    return user1, user2, dm, messages, search_manager


@pytest.fixture
def users_with_dm_and_reaction(messaging_manager, reaction_manager, two_users):
    """Create two users with a DM and a message for reactions testing."""
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    msg = messaging_manager.send_message(user1.id, dm.id, "Test message")
    return user1, user2, dm, msg, reaction_manager


@pytest.fixture
def group_with_message(messaging_manager, reaction_manager, rel_manager, three_users):
    """Create a group with owner, members, and a message for reactions testing."""
    owner, member1, member2 = three_users
    group = messaging_manager.create_group(
        owner.id, "Test Group", [member1.id, member2.id]
    )
    msg = messaging_manager.send_message(owner.id, group.id, "Group message")
    return owner, member1, member2, group, msg, messaging_manager, reaction_manager


@pytest.fixture
def users_with_server(server_manager, messaging_manager, reaction_manager, two_users):
    """Create a server with owner, member, group, and message for reactions testing."""
    owner, member = two_users
    server = server_manager.create_server(owner.id, "Test Server")
    server_manager.add_member(server.id, member.id)

    group = messaging_manager.create_group(owner.id, "Server Group", [member.id])
    msg = messaging_manager.send_message(owner.id, group.id, "Server message")

    return owner, member, server, group, msg, server_manager, reaction_manager


@pytest.fixture
def users_with_server_search(server_manager, search_manager, auth_manager):
    """Create a server with owner and 10 members for search testing (requires 10+ members to list)."""
    owner = auth_manager.register(
        username="search_owner",
        email="search_owner@example.com",
        password="TestPass123!",
    )
    server = server_manager.create_server(owner.id, "Test Server")

    members = []
    for i in range(10):
        member = auth_manager.register(
            username=f"search_member_{i}",
            email=f"search_member_{i}@example.com",
            password="TestPass123!",
        )
        server_manager.add_member(server.id, member.id)
        members.append(member)

    return owner, members, server, server_manager, search_manager


@pytest.fixture
def fresh_users_with_dm_and_relationships(
    messaging_manager, reaction_manager, rel_manager, two_users
):
    """Create two fresh users with a DM, message, and relationships manager for reactions testing."""
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    msg = messaging_manager.send_message(user1.id, dm.id, "Test message")
    return user1, user2, dm, msg, reaction_manager, rel_manager


@pytest.fixture
def server_with_voice(server_manager, voice_manager, three_users):
    """Create a server with voice channels for voice testing."""
    owner, member1, member2 = three_users
    server = server_manager.create_server(owner.id, "Test Server")
    server_manager.add_member(server.id, member1.id)
    server_manager.add_member(server.id, member2.id)

    voice_channel = server_manager.create_channel(
        owner.id, server.id, "voice", channel_type=server_manager.ChannelType.VOICE
    )
    stage_channel = server_manager.create_channel(
        owner.id, server.id, "stage", channel_type=server_manager.ChannelType.STAGE
    )

    return (
        owner,
        member1,
        member2,
        server,
        voice_channel,
        stage_channel,
        server_manager,
        voice_manager,
    )


@pytest.fixture
def server_with_moderator(server_manager, voice_manager, three_users):
    """Create a server with a moderator for voice testing."""
    owner, moderator, member = three_users
    server = server_manager.create_server(owner.id, "Test Server")
    server_manager.add_member(server.id, moderator.id)
    server_manager.add_member(server.id, member.id)

    # Give moderator role
    mod_role = server_manager.create_role(owner.id, server.id, "Moderator")
    server_manager.add_role_to_user(owner.id, server.id, moderator.id, mod_role.id)

    voice_channel = server_manager.create_channel(
        owner.id, server.id, "voice", channel_type=server_manager.ChannelType.VOICE
    )
    stage_channel = server_manager.create_channel(
        owner.id, server.id, "stage", channel_type=server_manager.ChannelType.STAGE
    )

    return (
        owner,
        moderator,
        member,
        server,
        voice_channel,
        stage_channel,
        server_manager,
        voice_manager,
    )


@pytest.fixture
def server_with_channel(server_manager, thread_manager, three_users):
    """Create a server with a text channel for threads testing."""
    owner, member1, member2 = three_users
    server = server_manager.create_server(owner.id, "Test Server")
    server_manager.add_member(server.id, member1.id)
    server_manager.add_member(server.id, member2.id)

    channel = server_manager.create_channel(
        owner.id, server.id, "general", channel_type=server_manager.ChannelType.TEXT
    )

    return owner, member1, member2, server, channel, server_manager, thread_manager


@pytest.fixture
def server_with_channels(server_manager, three_users):
    """Create a server with multiple channels for audit log testing."""
    owner, member1, member2 = three_users
    server = server_manager.create_server(owner.id, "Test Server")
    server_manager.add_member(server.id, member1.id)
    server_manager.add_member(server.id, member2.id)

    general = server_manager.create_channel(
        owner.id, server.id, "general", channel_type=server_manager.ChannelType.TEXT
    )
    voice = server_manager.create_channel(
        owner.id, server.id, "voice", channel_type=server_manager.ChannelType.VOICE
    )

    return owner, member1, member2, server, general, voice, server_manager


@pytest.fixture
def server_with_members(server_manager, three_users):
    """Create a server with members for audit log testing."""
    owner, member1, member2 = three_users
    server = server_manager.create_server(owner.id, "Test Server")
    server_manager.add_member(server.id, member1.id)
    server_manager.add_member(server.id, member2.id)

    return owner, member1, member2, server, server_manager


@pytest.fixture
def fresh_server(db, server_manager, webhook_manager, three_users):
    """Create a fresh server with channel for webhook testing."""
    owner, member, non_member = three_users
    server = server_manager.create_server(owner.id, "Test Server")
    server_manager.add_member(server.id, member.id)

    # Disable encryption for tests to avoid keyring issues
    server_manager._encrypt_descriptions = False

    channel = server_manager.create_channel(
        owner.id, server.id, "general", channel_type=server_manager.ChannelType.TEXT
    )

    # Return dict format for webhook tests
    return {
        "server": server,
        "owner": owner,
        "member": member,
        "non_member": non_member,
        "channel": channel,
        "servers": server_manager,
        "webhooks": webhook_manager,
    }


@pytest.fixture
def fresh_server_tuple(db, server_manager, two_users):
    """Create a fresh server with channel for servers testing (tuple format)."""
    owner, member = two_users
    server = server_manager.create_server(owner.id, "Test Server")
    server_manager.add_member(server.id, member.id)

    # Disable encryption for tests to avoid keyring issues
    server_manager._encrypt_descriptions = False

    channel = server_manager.create_channel(
        owner.id, server.id, "general", channel_type=server_manager.ChannelType.TEXT
    )

    # Return tuple format for backward compatibility with servers tests
    return server, owner, server_manager


@pytest.fixture
def webhook_with_token(fresh_server):
    """Create a webhook with token for testing."""
    webhook = fresh_server["webhooks"].create_webhook(
        user_id=fresh_server["owner"].id,
        channel_id=fresh_server["channel"].id,
        name="Test Webhook",
    )

    return {
        **fresh_server,
        "webhook": webhook,
        "token": webhook.token,
    }


@pytest.fixture
def dm_with_message(auth_manager, messaging_manager):
    """Create a DM with a message for polls testing."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register("polluser1", "poll1@example.com", "Password123!")
        user2 = auth_manager.register("polluser2", "poll2@example.com", "Password123!")

    dm = messaging_manager.create_dm(user1.id, user2.id)
    msg = messaging_manager.send_message(user1.id, dm.id, "Test message")

    from src.core.polls.manager import PollManager

    polls = PollManager(messaging_manager.db)

    return user1, user2, dm, msg, polls, messaging_manager


@pytest.fixture
def poll_with_options(dm_with_message):
    """Create a poll with options for testing."""
    user1, user2, dm, msg, polls, messaging = dm_with_message

    poll = polls.create_poll(
        user_id=user1.id,
        message_id=msg.id,
        question="What is your favorite color?",
        options=["Red", "Blue", "Green"],
    )

    return poll


@pytest.fixture
def fresh_users(auth_manager, presence_manager):
    """Create two fresh users with presence manager for activity testing."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username=f"activity_user1_{uuid.uuid4().hex[:8]}",
            email=f"activity1_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"activity_user2_{uuid.uuid4().hex[:8]}",
            email=f"activity2_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

    return user1, user2, presence_manager


@pytest.fixture
def friends_pair(auth_manager, rel_manager, presence_manager):
    """Create two users who are friends for presence testing."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username=f"friend1_{uuid.uuid4().hex[:8]}",
            email=f"friend1_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"friend2_{uuid.uuid4().hex[:8]}",
            email=f"friend2_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

    # Send friend request and get the request ID
    request = rel_manager.send_friend_request(user1.id, user2.id)
    # Accept friend request (user2 accepts, using the request ID)
    rel_manager.accept_friend_request(user2.id, request.id)

    return user1, user2, rel_manager, presence_manager


# =============================================================================
# API Testing Fixtures
# =============================================================================


@pytest.fixture
def test_client(
    db,
    auth_manager,
    messaging_manager,
    server_manager,
    rel_manager,
    presence_manager,
    reaction_manager,
    reports,
    feedback,
):
    """Create a FastAPI test client with rate limiting disabled."""
    from fastapi.testclient import TestClient
    from src.api import create_app, setup
    from src.core import (
        ratelimit,
        embeds,
        webhooks,
        notifications,
        threads,
        media,
        settings,
        events,
    )
    from src.core.ratelimit.storage import MemoryStorage

    # Setup rate limiting with memory storage
    storage = MemoryStorage(cleanup_interval=1.0, max_buckets=1000)
    ratelimit.setup(
        storage_backend=storage,
        bot_multiplier=1.5,
        enable_global_limit=True,
    )

    # Setup embeds
    embeds.setup(db, messaging_manager, server_manager)

    # Setup webhooks
    webhooks.setup(db, auth_manager, messaging_manager, server_manager, embeds._manager)

    # Setup threads
    threads.setup(db, auth_manager, messaging_manager, server_manager)

    # Setup notifications
    notifications.setup(
        db,
        auth_manager,
        messaging_manager,
        server_manager,
        rel_manager,
        presence_manager,
    )

    # Setup media
    media.setup(db, messaging_manager)

    # Setup settings
    settings.setup(db)

    # Setup events
    events.setup(rel_manager, server_manager, messaging_manager)

    # Setup API with all modules
    setup(
        db=db,
        auth_module=auth_manager,
        messaging_module=messaging_manager,
        servers_module=server_manager,
        relationships_module=rel_manager,
        presence_module=presence_manager,
        reactions_module=reaction_manager,
        embeds_module=embeds._manager,
        webhooks_module=webhooks._manager,
        settings_module=settings._manager,
        threads_module=threads._manager,
        notifications_module=notifications._manager,
        media_module=media._manager,
        events_module=events._manager,
        reports_module=reports,
        feedback_module=feedback,
    )

    app = create_app(enable_rate_limiting=False)
    return TestClient(app)


@pytest.fixture
def rate_limit_client(
    db,
    auth_manager,
    messaging_manager,
    server_manager,
    rel_manager,
    presence_manager,
    reaction_manager,
    reports,
    feedback,
):
    """Create a FastAPI test client with rate limiting enabled."""
    from fastapi.testclient import TestClient
    from src.api import create_app, setup
    from src.core import (
        ratelimit,
        embeds,
        webhooks,
        notifications,
        threads,
        media,
        settings,
        events,
    )
    from src.core.ratelimit.storage import MemoryStorage

    # Setup rate limiting with memory storage
    storage = MemoryStorage(cleanup_interval=1.0, max_buckets=1000)
    ratelimit.setup(
        storage_backend=storage,
        bot_multiplier=1.5,
        enable_global_limit=True,
    )

    # Setup embeds
    embeds.setup(db, messaging_manager, server_manager)

    # Setup webhooks
    webhooks.setup(db, auth_manager, messaging_manager, server_manager, embeds._manager)

    # Setup threads
    threads.setup(db, auth_manager, messaging_manager, server_manager)

    # Setup notifications
    notifications.setup(
        db,
        auth_manager,
        messaging_manager,
        server_manager,
        rel_manager,
        presence_manager,
    )

    # Setup media
    media.setup(db, messaging_manager)

    # Setup settings
    settings.setup(db)

    # Setup events
    events.setup(rel_manager, server_manager, messaging_manager)

    # Setup API with all modules
    setup(
        db=db,
        auth_module=auth_manager,
        messaging_module=messaging_manager,
        servers_module=server_manager,
        relationships_module=rel_manager,
        presence_module=presence_manager,
        reactions_module=reaction_manager,
        embeds_module=embeds._manager,
        webhooks_module=webhooks._manager,
        settings_module=settings._manager,
        threads_module=threads._manager,
        notifications_module=notifications._manager,
        media_module=media._manager,
        events_module=events._manager,
        reports_module=reports,
        feedback_module=feedback,
    )

    app = create_app(enable_rate_limiting=True)
    return TestClient(app)


@pytest.fixture
def auth_headers(test_user_with_token):
    """Get authorization headers for authenticated requests."""
    user_data = test_user_with_token
    token = user_data["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_module():
    """API module fixture for testing."""
    # This is a marker fixture - the actual API setup is done in the test_client fixture
    # Just return None to satisfy the fixture requirement
    return None


# =============================================================================
# Backward Compatibility Fixtures
# =============================================================================


class ModuleRegistry:
    """
    Backward compatibility wrapper for module fixtures.

    Provides attribute access to individual manager fixtures.
    """

    def __init__(
        self,
        auth_manager,
        messaging_manager,
        server_manager,
        presence_manager,
        rel_manager,
        reaction_manager,
        webhook_manager,
        thread_manager,
        notification_manager,
        media_manager,
        search_manager,
        app_manager,
        sticker_manager,
        poll_manager,
        soundboard_manager,
        reports,
        feedback,
    ):
        self.auth = auth_manager
        self.messaging = messaging_manager
        self.servers = server_manager
        self.presence = presence_manager
        self.relationships = rel_manager
        self.reactions = reaction_manager
        self.webhooks = webhook_manager
        self.threads = thread_manager
        self.notifications = notification_manager
        self.media = media_manager
        self.search = search_manager
        self.applications = app_manager
        self.stickers = sticker_manager
        self.polls = poll_manager
        self.soundboard = soundboard_manager
        self.reports = reports
        self.feedback = feedback


@pytest.fixture
def modules(
    auth_manager,
    messaging_manager,
    server_manager,
    presence_manager,
    rel_manager,
    reaction_manager,
    webhook_manager,
    thread_manager,
    notification_manager,
    media_manager,
    search_manager,
    app_manager,
    sticker_manager,
    poll_manager,
    soundboard_manager,
    reports,
    feedback,
):
    """
    Backward compatibility fixture that wraps all module managers.

    Returns a ModuleRegistry instance that provides attribute access to all managers.
    Example: modules.auth, modules.messaging, etc.
    """
    return ModuleRegistry(
        auth_manager=auth_manager,
        messaging_manager=messaging_manager,
        server_manager=server_manager,
        presence_manager=presence_manager,
        rel_manager=rel_manager,
        reaction_manager=reaction_manager,
        webhook_manager=webhook_manager,
        thread_manager=thread_manager,
        notification_manager=notification_manager,
        media_manager=media_manager,
        search_manager=search_manager,
        app_manager=app_manager,
        sticker_manager=sticker_manager,
        poll_manager=poll_manager,
        soundboard_manager=soundboard_manager,
        reports=reports,
        feedback=feedback,
    )


class UserPool:
    """
    Backward compatibility wrapper for user management.

    Provides similar interface to the old user_pool fixture.
    """

    def __init__(self, auth_manager):
        self.auth_manager = auth_manager
        self._users = {}

    def create_user(self, username=None, email=None, password="TestPassword123!"):
        """Create a new user and store in pool."""
        from unittest.mock import patch
        import uuid

        if username is None:
            username = f"testuser_{uuid.uuid4().hex[:8]}"
        if email is None:
            email = f"test_{uuid.uuid4().hex[:8]}@example.com"

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = self.auth_manager.register(
                username=username,
                email=email,
                password=password,
            )
        self._users[user.id] = user
        return user

    def get_user(self, user_id):
        """Get a user from the pool by ID."""
        return self._users.get(user_id)

    def get_all_users(self):
        """Get all users in the pool."""
        return list(self._users.values())


@pytest.fixture
def user_pool(auth_manager):
    """
    Backward compatibility fixture for user management.

    Returns a UserPool instance that provides methods for creating and managing test users.
    """
    return UserPool(auth_manager)


@pytest.fixture
def db_and_modules(db, auth_manager, messaging_manager, server_manager, embeds_manager):
    """
    Backward compatibility fixture that returns db and module managers as a tuple.

    Returns: (db, auth_manager, messaging_manager, server_manager, embeds_manager)
    """
    return db, auth_manager, messaging_manager, server_manager, embeds_manager


@pytest.fixture
def db_and_search(db, auth_manager, messaging_manager, server_manager, search_manager):
    """
    Fixture that returns db and search-related module managers as a tuple.

    Returns: (db, auth_manager, messaging_manager, server_manager, search_manager)
    """
    return db, auth_manager, messaging_manager, server_manager, search_manager


@pytest.fixture
def db_and_auth(db, auth_manager):
    """
    Backward compatibility fixture that returns both db and auth manager.

    Returns a tuple: (db, auth_manager)
    """
    return db, auth_manager


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def email_sender():
    """Mock email sender."""
    mock = Mock()
    mock.send = Mock(return_value=True)
    return mock


@pytest.fixture
def mocker():
    """Mocker fixture for patching."""
    from unittest.mock import MagicMock, patch

    class Mocker:
        def __init__(self):
            self._patches = []
            self.MagicMock = MagicMock

        def patch(self, *args, **kwargs):
            p = patch(*args, **kwargs)
            started = p.start()
            self._patches.append(p)
            return started

        def stopall(self):
            while self._patches:
                p = self._patches.pop()
                try:
                    p.stop()
                except Exception:
                    pass

    m = Mocker()
    try:
        yield m
    finally:
        m.stopall()


# =============================================================================
# Rate Limiting Fixtures
# =============================================================================


@pytest.fixture
def memory_storage():
    """MemoryStorage fixture for rate limiting tests."""
    from src.core.ratelimit.storage import MemoryStorage

    return MemoryStorage(cleanup_interval=1.0, max_buckets=1000)


@pytest.fixture
def rate_limit_manager(memory_storage):
    """RateLimitManager fixture."""
    from src.core.ratelimit.manager import RateLimitManager

    return RateLimitManager(memory_storage)


@pytest.fixture
def setup_ratelimit(memory_storage):
    """Setup ratelimit module for module-level interface tests."""
    from src.core import ratelimit

    ratelimit.setup(
        storage_backend=memory_storage,
        bot_multiplier=1.5,
        enable_global_limit=True,
    )
    return ratelimit


@pytest.fixture
def test_user_id():
    """Test user ID fixture."""
    return 12345


@pytest.fixture
def test_channel_id():
    """Test channel ID fixture."""
    return 67890


@pytest.fixture
def test_webhook_id():
    """Test webhook ID fixture."""
    return 54321


# =============================================================================
# Pytest Configuration
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
    config.addinivalue_line(
        "markers", "production_simulation: Production simulation tests"
    )
    config.addinivalue_line("markers", "multiprocess: Multiprocess tests")
    config.addinivalue_line("markers", "requires_postgres: Tests requiring PostgreSQL")


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests based on their location."""
    for item in items:
        item_path = str(item.fspath).replace(os.sep, "/")

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
