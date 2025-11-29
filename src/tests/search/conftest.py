"""
Shared fixtures for search tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def db_and_modules(modules):
    """Legacy fixture for backward compatibility."""
    return modules._db, modules.auth, modules.messaging, modules.servers, modules.search


@pytest.fixture
def users_with_dm(modules, user_pool):
    """Create users with a DM conversation and messages."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()

    dm = modules.messaging.create_dm(user1.id, user2.id)
    
    msg1 = modules.messaging.send_message(user1.id, dm.id, "Hello world from Alice")
    msg2 = modules.messaging.send_message(user2.id, dm.id, "Hi Alice, this is Bob")
    msg3 = modules.messaging.send_message(user1.id, dm.id, "Check out this link https://example.com")
    
    modules.search.index_message(msg1.id, msg1.content, {
        "author_id": user1.id,
        "conversation_id": dm.id,
        "created_at": msg1.created_at,
    })
    modules.search.index_message(msg2.id, msg2.content, {
        "author_id": user2.id,
        "conversation_id": dm.id,
        "created_at": msg2.created_at,
    })
    modules.search.index_message(msg3.id, msg3.content, {
        "author_id": user1.id,
        "conversation_id": dm.id,
        "created_at": msg3.created_at,
        "has_links": True,
    })

    return user1, user2, dm, [msg1, msg2, msg3], modules.search


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

    return owner, member1, member2, server, modules.servers, modules.search


@pytest.fixture
def indexed_users(modules):
    """Create and index users for user search tests."""
    unique_id = uuid.uuid4().hex[:8]

    users = []
    for name in ["alice", "bob", "charlie", "david", "eve"]:
        user = modules.auth.register(
            username=f"{name}_{unique_id}",
            email=f"{name}_{unique_id}@example.com",
            password="TestPass123!"
        )
        modules.search._get_manager()._indexer.index_user(
            modules.search.models.IndexedUser(
                user_id=user.id,
                username=user.username,
                display_name=name.capitalize(),
            )
        )
        users.append(user)

    return users, modules.search
