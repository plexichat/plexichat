"""
Shared fixtures for server tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def users(modules, user_pool):
    """Get test users."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()
    user3 = user_pool.get_user()
    user4 = user_pool.get_user()
    return user1, user2, user3, user4, modules.servers


@pytest.fixture
def server_with_members(modules, user_pool):
    """Create a server with owner, admin, and member."""
    owner = user_pool.get_user()
    admin_user = user_pool.get_user()
    member_user = user_pool.get_user()
    outsider = user_pool.get_user()

    unique_id = uuid.uuid4().hex[:6]
    server = modules.servers.create_server(
        owner_id=owner.id,
        name=f"Test Server {unique_id}",
        description="A test server"
    )

    # Add admin and member
    modules.servers.add_member(server.id, admin_user.id)
    modules.servers.add_member(server.id, member_user.id)

    # Create admin role and assign
    admin_role = modules.servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Admin",
        permissions={
            "administrator": False,
            "channels.manage": True,
            "members.kick": True,
            "members.ban": True,
            "members.manage_roles": True,
            "messages.manage": True,
        },
        color="#FF0000",
        hoist=True
    )

    modules.servers.assign_role(owner.id, server.id, admin_user.id, admin_role.id)

    return server, owner, admin_user, member_user, outsider, admin_role, modules.servers


@pytest.fixture
def fresh_server(modules, user_pool):
    """Create a fresh server for tests needing isolation."""
    owner = user_pool.get_user()

    unique_id = uuid.uuid4().hex[:6]
    server = modules.servers.create_server(
        owner_id=owner.id,
        name=f"Fresh Server {unique_id}",
        description="A fresh test server"
    )

    return server, owner, modules.servers


@pytest.fixture
def server_with_channels(server_with_members):
    """Create a server with multiple channels."""
    server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

    # Create category
    category = servers.create_category(
        user_id=owner.id,
        server_id=server.id,
        name="Text Channels"
    )

    # Create channels
    general = servers.get_channels(owner.id, server.id)[0]  # Default general channel

    announcements = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="announcements",
        category_id=category.id
    )

    private = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="private",
        category_id=category.id
    )

    return server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers
