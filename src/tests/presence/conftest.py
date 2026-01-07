"""
Shared fixtures for presence tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def db_and_modules(modules):
    """Legacy fixture for backward compatibility."""
    return (
        modules._db,
        modules.auth,
        modules.servers,
        modules.relationships,
        modules.presence,
    )


@pytest.fixture
def users(modules, user_pool):
    """Get test users."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()
    user3 = user_pool.get_user()
    user4 = user_pool.get_user()
    return user1, user2, user3, user4, modules.presence


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

    return user1, user2, modules.presence


@pytest.fixture
def friends_pair(modules):
    """Create two users who are already friends."""
    unique_id = uuid.uuid4().hex[:8]

    user1 = modules.auth.register(
        username=f"friend1_{unique_id}",
        email=f"friend1_{unique_id}@example.com",
        password="TestPass123!",
    )

    user2 = modules.auth.register(
        username=f"friend2_{unique_id}",
        email=f"friend2_{unique_id}@example.com",
        password="TestPass123!",
    )

    # Make them friends
    request = modules.relationships.send_friend_request(user1.id, user2.id)
    modules.relationships.accept_friend_request(user2.id, request.id)

    return user1, user2, modules.relationships, modules.presence


@pytest.fixture
def blocked_pair(modules):
    """Create two users where one has blocked the other."""
    unique_id = uuid.uuid4().hex[:8]

    blocker = modules.auth.register(
        username=f"blocker_{unique_id}",
        email=f"blocker_{unique_id}@example.com",
        password="TestPass123!",
    )

    blocked = modules.auth.register(
        username=f"blocked_{unique_id}",
        email=f"blocked_{unique_id}@example.com",
        password="TestPass123!",
    )

    # Block the user
    modules.relationships.block_user(blocker.id, blocked.id)

    return blocker, blocked, modules.relationships, modules.presence


@pytest.fixture
def users_with_server(modules):
    """Create users who share a server."""
    unique_id = uuid.uuid4().hex[:8]

    user1 = modules.auth.register(
        username=f"srv1_{unique_id}",
        email=f"srv1_{unique_id}@example.com",
        password="TestPass123!",
    )

    user2 = modules.auth.register(
        username=f"srv2_{unique_id}",
        email=f"srv2_{unique_id}@example.com",
        password="TestPass123!",
    )

    user3 = modules.auth.register(
        username=f"srv3_{unique_id}",
        email=f"srv3_{unique_id}@example.com",
        password="TestPass123!",
    )

    # Create server and add members
    server = modules.servers.create_server(user1.id, f"Test Server {unique_id}")
    modules.servers.add_member(server.id, user2.id)
    modules.servers.add_member(server.id, user3.id)

    return user1, user2, user3, server, modules.servers, modules.presence
