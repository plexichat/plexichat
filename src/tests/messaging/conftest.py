"""
Shared fixtures for messaging tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def dm_conversation(modules, user_pool):
    """Create a DM conversation between two users."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()

    dm = modules.messaging.create_dm(user1.id, user2.id)

    return dm, user1, user2, modules.messaging


@pytest.fixture
def fresh_dm(modules):
    """Create a fresh DM with new users for tests needing isolation."""
    unique_id = uuid.uuid4().hex[:8]

    new_user1 = modules.auth.register(
        username=f"fresh1_{unique_id}",
        email=f"fresh1_{unique_id}@example.com",
        password="TestPass123!",
    )
    new_user2 = modules.auth.register(
        username=f"fresh2_{unique_id}",
        email=f"fresh2_{unique_id}@example.com",
        password="TestPass123!",
    )

    dm = modules.messaging.create_dm(new_user1.id, new_user2.id)

    return dm, new_user1, new_user2, modules.messaging


@pytest.fixture
def group_conversation(modules, user_pool):
    """Create a group conversation."""
    owner = user_pool.get_user()
    member1 = user_pool.get_user()
    member2 = user_pool.get_user()

    unique_id = uuid.uuid4().hex[:6]
    group = modules.messaging.create_group(
        owner_id=owner.id,
        name=f"Test Group {unique_id}",
        participant_ids=[member1.id, member2.id],
    )

    return group, owner, member1, member2, modules.messaging


@pytest.fixture
def users(modules, user_pool):
    """Get test users."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()
    user3 = user_pool.get_user()
    return user1, user2, user3, modules.messaging
