"""User / server / conversation scenario fixtures."""

import pytest
import uuid
from unittest.mock import patch
from src.utils import encryption


# =============================================================================
# Helper Fixtures for Creating Test Data
# =============================================================================


class TestPIIGenerator:
    """Helper to generate non-PII test data for tests."""

    def __init__(self):
        self._counter = 0

    def email(self) -> str:
        """Generate a test email that doesn't contain real PII.

        Uses ``.test.local`` (RFC 6761 reserved + dot-local TLD) so the
        address always passes the conftest bypass and tricky regex
        validators in the auth stack.  Adjust the suffix in lockstep
        with the conftest validator whitelist if you change it.
        """
        self._counter += 1
        return f"test_user_{self._counter}_{uuid.uuid4().hex[:8]}@test.local"

    def dob(self) -> str:
        """Generate a static non-PII date of birth."""
        return "2000-01-01"

    def ip(self) -> str:
        """Generate a test IP address."""
        return f"127.0.0.{self._counter % 255}"

    def user_agent(self) -> str:
        """Generate a test user agent."""
        return "TestAgent/1.0 (Plexichat; Testing)"


@pytest.fixture(scope="session")
def pii_gen():
    """PII generator fixture."""
    return TestPIIGenerator()


@pytest.fixture
def test_user(auth_manager, pii_gen):
    """Create a test user with fake hashing and non-PII data."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return user


@pytest.fixture
def registered_user(auth_manager, pii_gen):
    """Create a registered user for tests (returns tuple: user, auth_manager, username)."""
    username = f"testuser_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return user, auth_manager, username


@pytest.fixture
def logged_in_user(auth_manager, pii_gen):
    """Create a logged-in user for tests (returns tuple: user, token, auth_manager, username)."""
    username = f"testuser_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=pii_gen.email(),
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return user, result.token, auth_manager, username


@pytest.fixture
def test_user_with_token(auth_manager, test_user):
    """Create a test user and return them with their auth token (dict format for compatibility)."""
    # The api conftest overrides test_user as a credentials dict; the root
    # conftest returns a User object. Normalize to a dict either way.
    if isinstance(test_user, dict):
        user = test_user["user"]
        username = test_user["username"]
        password = test_user["password"]
    else:
        user = test_user
        username = test_user.username
        password = "TestPass123!"
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, password)
    return {
        "user": user,
        "token": result.token,
        "username": username,
        "password": password,
    }


@pytest.fixture
def two_users(auth_manager, pii_gen):
    """Create two test users."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return user1, user2


@pytest.fixture
def three_users(auth_manager, pii_gen):
    """Create three test users."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        user3 = auth_manager.register(
            username=f"testuser_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
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
def fresh_users_with_dm(auth_manager, messaging_manager, embeds_manager, pii_gen):
    """Create two fresh users with a DM and a message for embeds testing."""
    # Create two users with fake hashing
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username=f"embed_user1_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"embed_user2_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
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
def users_with_server_search(server_manager, search_manager, auth_manager, pii_gen):
    """Create a server with owner and 10 members for search testing (requires 10+ members to list)."""
    owner = auth_manager.register(
        username="search_owner",
        email=pii_gen.email(),
        password="TestPass123!",
    )
    server = server_manager.create_server(owner.id, "Test Server")

    members = []
    for i in range(10):
        member = auth_manager.register(
            username=f"search_member_{i}",
            email=pii_gen.email(),
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
        owner.id, server.id, "voice-chat", channel_type=server_manager.ChannelType.VOICE
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

    # Give moderator role with standard voice moderation permissions
    mod_role = server_manager.create_role(
        owner.id,
        server.id,
        "Moderator",
        permissions={
            "voice.mute_members": True,
            "voice.deafen_members": True,
            "voice.move_members": True,
        },
    )
    server_manager.assign_role(owner.id, server.id, moderator.id, mod_role.id)

    voice_channel = server_manager.create_channel(
        owner.id, server.id, "voice-chat", channel_type=server_manager.ChannelType.VOICE
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

    server_manager.create_channel(
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

    polls = PollManager(messaging_manager.db, messaging_module=messaging_manager)

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
