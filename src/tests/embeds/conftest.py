"""
Shared fixtures for embed tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


def _init_embeds(modules):
    """Reliably initialize the embeds module."""
    from src.core import embeds
    embeds._manager = None
    embeds._setup_complete = False
    embeds.setup(modules._db, modules.messaging, modules.servers)
    return embeds


@pytest.fixture
def db_and_modules(modules):
    """Legacy fixture for backward compatibility."""
    embeds = _init_embeds(modules)
    return modules._db, modules.auth, modules.messaging, modules.servers, embeds


@pytest.fixture
def users_with_dm(modules, user_pool):
    """Create users with a DM conversation and message."""
    embeds = _init_embeds(modules)
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()

    dm = modules.messaging.create_dm(user1.id, user2.id)
    msg = modules.messaging.send_message(user1.id, dm.id, "Test message for embeds")

    return user1, user2, dm, msg, embeds


@pytest.fixture
def fresh_users_with_dm(modules):
    """Create fresh users with DM for isolated tests."""
    embeds = _init_embeds(modules)
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

    return user1, user2, dm, msg, embeds, modules.messaging


@pytest.fixture
def users_with_server(modules):
    """Create users with a server for testing."""
    embeds = _init_embeds(modules)
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
    msg = modules.messaging.send_message(owner.id, group.id, "Server message for embeds")

    return owner, member, server, group, msg, modules.servers, embeds, modules.messaging


@pytest.fixture
def group_with_message(modules):
    """Create a group conversation with a message."""
    embeds = _init_embeds(modules)
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

    group = modules.messaging.create_group(owner.id, f"Test Group {unique_id}", [member1.id])
    msg = modules.messaging.send_message(owner.id, group.id, "Group message for embeds")

    return owner, member1, group, msg, modules.messaging, embeds
