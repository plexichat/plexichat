"""
Shared fixtures for notification tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def db_and_modules(modules):
    """Legacy fixture for backward compatibility."""
    return modules._db, modules.auth, modules.messaging, modules.servers, modules.relationships, modules.presence, modules.notifications


@pytest.fixture
def users_with_dm(modules, user_pool):
    """Create users with a DM conversation."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()

    dm = modules.messaging.create_dm(user1.id, user2.id)

    return user1, user2, dm, modules.messaging, modules.notifications


@pytest.fixture
def fresh_users(modules):
    """Create fresh users for isolated tests."""
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

    return user1, user2, modules.auth, modules.messaging, modules.servers, modules.relationships, modules.presence, modules.notifications


@pytest.fixture
def users_with_server(modules):
    """Create users with a server for testing."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"owner_{unique_id}",
        email=f"owner_{unique_id}@example.com",
        password="TestPass123!"
    )

    member1 = modules.auth.register(
        username=f"member1_{unique_id}",
        email=f"member1_{unique_id}@example.com",
        password="TestPass123!"
    )

    member2 = modules.auth.register(
        username=f"member2_{unique_id}",
        email=f"member2_{unique_id}@example.com",
        password="TestPass123!"
    )

    server = modules.servers.create_server(owner.id, f"Test Server {unique_id}")
    modules.servers.add_member(server.id, member1.id)
    modules.servers.add_member(server.id, member2.id)

    channel = modules.servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="general",
        channel_type=modules.servers.ChannelType.TEXT
    )

    return owner, member1, member2, server, channel, modules.servers, modules.messaging, modules.notifications


@pytest.fixture
def users_with_role(users_with_server):
    """Create users with a server and role."""
    owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="TestRole",
        permissions={},
        mentionable=True
    )

    servers.assign_role(owner.id, server.id, member1.id, role.id)

    return owner, member1, member2, server, channel, role, servers, messaging, notifications


@pytest.fixture
def group_conversation(modules):
    """Create a group conversation for testing."""
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

    return owner, member1, member2, group, modules.messaging, modules.notifications, modules.relationships
