"""
Shared fixtures for thread tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def db_and_modules(modules):
    """Legacy fixture for backward compatibility."""
    return modules._db, modules.auth, modules.messaging, modules.servers, modules.threads


@pytest.fixture
def users(modules, user_pool):
    """Get test users."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()
    user3 = user_pool.get_user()
    user4 = user_pool.get_user()
    return user1, user2, user3, user4, modules.threads


@pytest.fixture
def fresh_users(modules):
    """Create fresh users for tests needing isolation."""
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

    return user1, user2, modules.threads


@pytest.fixture
def server_with_channel(modules):
    """Create a server with a text channel for threads."""
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

    server = modules.servers.create_server(owner.id, f"Thread Test Server {unique_id}")
    modules.servers.add_member(server.id, member1.id)
    modules.servers.add_member(server.id, member2.id)

    # Create a member role with thread creation permissions
    member_role = modules.servers.create_role(
        owner.id, server.id, "Member",
        permissions={
            "threads.create_public": True,
        }
    )
    modules.servers.assign_role(owner.id, server.id, member1.id, member_role.id)
    modules.servers.assign_role(owner.id, server.id, member2.id, member_role.id)

    channel = modules.servers.create_channel(
        owner.id, server.id, "general",
        channel_type=modules.servers.ChannelType.TEXT
    )

    return owner, member1, member2, server, channel, modules.servers, modules.threads


@pytest.fixture
def server_with_moderator(modules):
    """Create a server with a moderator role."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"modowner_{unique_id}",
        email=f"modowner_{unique_id}@example.com",
        password="TestPass123!"
    )

    moderator = modules.auth.register(
        username=f"mod_{unique_id}",
        email=f"mod_{unique_id}@example.com",
        password="TestPass123!"
    )

    member = modules.auth.register(
        username=f"modmember_{unique_id}",
        email=f"modmember_{unique_id}@example.com",
        password="TestPass123!"
    )

    server = modules.servers.create_server(owner.id, f"Mod Test Server {unique_id}")
    modules.servers.add_member(server.id, moderator.id)
    modules.servers.add_member(server.id, member.id)

    mod_role = modules.servers.create_role(
        owner.id, server.id, "Moderator",
        permissions={
            "threads.manage": True,
            "threads.create_public": True,
            "threads.create_private": True,
        }
    )
    modules.servers.assign_role(owner.id, server.id, moderator.id, mod_role.id)

    # Create a member role with thread creation permissions
    member_role = modules.servers.create_role(
        owner.id, server.id, "Member",
        permissions={
            "threads.create_public": True,
        }
    )
    modules.servers.assign_role(owner.id, server.id, member.id, member_role.id)

    channel = modules.servers.create_channel(
        owner.id, server.id, "mod-channel",
        channel_type=modules.servers.ChannelType.TEXT
    )

    return owner, moderator, member, server, channel, modules.servers, modules.threads
