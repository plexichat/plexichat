"""
Shared fixtures for thread tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


def _wired_thread_manager(modules):
    """Build a ThreadManager fully wired to auth/messaging/servers.

    The threads tests exercise real permission checks and message
    delivery, so the manager returned by ``server_with_channel`` /
    ``server_with_moderator`` must be wired (unlike the bare
    ``thread_manager`` fixture used by low-level unit tests).
    """
    from src.core.threads.manager import ThreadManager

    return ThreadManager(
        modules.threads._db,
        auth_module=modules.auth,
        messaging_module=modules.messaging,
        servers_module=modules.servers,
    )


_MEMBER_PERMS = {
    "channels.view": True,
    "messages.send": True,
    "messages.read_history": True,
    "threads.create_public": True,
}

_MODERATOR_PERMS = {
    "channels.view": True,
    "messages.send": True,
    "messages.read_history": True,
    "messages.bypass_slowmode": True,
    "threads.create_public": True,
    "threads.create_private": True,
    "threads.manage": True,
}


@pytest.fixture
def db_and_modules(modules):
    """Legacy fixture for backward compatibility."""
    return (
        modules._db,
        modules.auth,
        modules.messaging,
        modules.servers,
        modules.threads,
    )


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
        password="TestPass123!",
    )

    user2 = modules.auth.register(
        username=f"fresh2_{unique_id}",
        email=f"fresh2_{unique_id}@example.com",
        password="TestPass123!",
    )

    return user1, user2, modules.threads


@pytest.fixture
def server_with_channel(modules):
    """Create a server with a text channel for threads."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"owner_{unique_id}",
        email=f"owner_{unique_id}@example.com",
        password="TestPass123!",
    )

    member1 = modules.auth.register(
        username=f"member1_{unique_id}",
        email=f"member1_{unique_id}@example.com",
        password="TestPass123!",
    )

    member2 = modules.auth.register(
        username=f"member2_{unique_id}",
        email=f"member2_{unique_id}@example.com",
        password="TestPass123!",
    )

    server = modules.servers.create_server(owner.id, f"Thread Test Server {unique_id}")
    modules.servers.add_member(server.id, member1.id)
    modules.servers.add_member(server.id, member2.id)

    # Create a member role with thread creation permissions
    member_role = modules.servers.create_role(
        owner.id,
        server.id,
        "Member",
        permissions=dict(_MEMBER_PERMS),
    )
    modules.servers.assign_role(owner.id, server.id, member1.id, member_role.id)
    modules.servers.assign_role(owner.id, server.id, member2.id, member_role.id)

    channel = modules.servers.create_channel(
        owner.id, server.id, "general", channel_type=modules.servers.ChannelType.TEXT
    )

    return (
        owner,
        member1,
        member2,
        server,
        channel,
        modules.servers,
        _wired_thread_manager(modules),
    )


@pytest.fixture
def server_with_moderator(modules):
    """Create a server with a moderator role."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"modowner_{unique_id}",
        email=f"modowner_{unique_id}@example.com",
        password="TestPass123!",
    )

    moderator = modules.auth.register(
        username=f"mod_{unique_id}",
        email=f"mod_{unique_id}@example.com",
        password="TestPass123!",
    )

    member = modules.auth.register(
        username=f"modmember_{unique_id}",
        email=f"modmember_{unique_id}@example.com",
        password="TestPass123!",
    )

    server = modules.servers.create_server(owner.id, f"Mod Test Server {unique_id}")
    modules.servers.add_member(server.id, moderator.id)
    modules.servers.add_member(server.id, member.id)

    mod_role = modules.servers.create_role(
        owner.id,
        server.id,
        "Moderator",
        permissions=dict(_MODERATOR_PERMS),
    )
    modules.servers.assign_role(owner.id, server.id, moderator.id, mod_role.id)

    # Create a member role with thread creation permissions
    member_role = modules.servers.create_role(
        owner.id,
        server.id,
        "Member",
        permissions={
            "channels.view": True,
            "messages.send": True,
            "messages.read_history": True,
            "threads.create_public": True,
            "threads.create_private": True,
        },
    )
    modules.servers.assign_role(owner.id, server.id, member.id, member_role.id)

    channel = modules.servers.create_channel(
        owner.id,
        server.id,
        "mod-channel",
        channel_type=modules.servers.ChannelType.TEXT,
    )

    return (
        owner,
        moderator,
        member,
        server,
        channel,
        modules.servers,
        _wired_thread_manager(modules),
    )
