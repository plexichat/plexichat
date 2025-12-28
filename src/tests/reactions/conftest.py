"""
Shared fixtures for reaction tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def sample_image():
    """Generate a minimal valid PNG image."""
    return (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00'
        b'\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc'
        b'\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND'
        b'\xaeB`\x82'
    )


@pytest.fixture
def sample_gif():
    """Generate a minimal valid animated GIF."""
    return (
        b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!'
        b'\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00'
        b'\x00\x02\x02D\x01\x00;'
    )


@pytest.fixture
def db_and_modules(modules):
    """Legacy fixture for backward compatibility."""
    return modules._db, modules.auth, modules.messaging, modules.servers, modules.relationships, modules.reactions


@pytest.fixture
def users_with_dm(modules, user_pool):
    """Create users with a DM conversation and message."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()

    dm = modules.messaging.create_dm(user1.id, user2.id)
    msg = modules.messaging.send_message(user1.id, dm.id, "Test message for reactions")

    return user1, user2, dm, msg, modules.reactions


@pytest.fixture
def fresh_users_with_dm(modules):
    """Create fresh users with DM for isolated tests."""
    unique_id = uuid.uuid4().hex[:8]

    user1 = modules.auth.register(
        username=f"fresh1_{unique_id}",
        email=f"fresh1_{unique_id}@example.com",
        password="TestPass123!"
    )

    user2 = modules.auth.register(
        username=f"fresh2_{unique_id}",
        email=f"fresh2_{unique_id}@example.com",
        password="TestPass123!"
    )

    dm = modules.messaging.create_dm(user1.id, user2.id)
    msg = modules.messaging.send_message(user1.id, dm.id, "Fresh test message")

    return user1, user2, dm, msg, modules.reactions, modules.relationships


@pytest.fixture
def users_with_server(modules):
    """Create users with a server and a group conversation for testing."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"owner_{unique_id}",
        email=f"owner_{unique_id}@example.com",
        password="TestPass123!"
    )

    member = modules.auth.register(
        username=f"member_{unique_id}",
        email=f"member_{unique_id}@example.com",
        password="TestPass123!"
    )

    server = modules.servers.create_server(owner.id, f"Test Server {unique_id}")
    modules.servers.add_member(server.id, member.id)

    group = modules.messaging.create_group(owner.id, f"Server Group {unique_id}", [member.id])
    msg = modules.messaging.send_message(owner.id, group.id, "Server message for reactions")

    return owner, member, server, group, msg, modules.servers, modules.reactions


@pytest.fixture
def group_with_message(modules):
    """Create a group conversation with a message."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"grp_owner_{unique_id}",
        email=f"grp_owner_{unique_id}@example.com",
        password="TestPass123!"
    )

    member1 = modules.auth.register(
        username=f"grp_mem1_{unique_id}",
        email=f"grp_mem1_{unique_id}@example.com",
        password="TestPass123!"
    )

    member2 = modules.auth.register(
        username=f"grp_mem2_{unique_id}",
        email=f"grp_mem2_{unique_id}@example.com",
        password="TestPass123!"
    )

    group = modules.messaging.create_group(owner.id, f"Test Group {unique_id}", [member1.id, member2.id])
    msg = modules.messaging.send_message(owner.id, group.id, "Group message for reactions")

    return owner, member1, member2, group, msg, modules.messaging, modules.reactions
