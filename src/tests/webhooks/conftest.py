"""
Shared fixtures for webhook tests.

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
        modules.messaging,
        modules.servers,
        modules.embeds,
        modules.webhooks,
    )


@pytest.fixture
def base_server_setup(modules):
    """Create base server with channel for webhook tests."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"owner_{unique_id}",
        email=f"owner_{unique_id}@example.com",
        password="TestPass123!",
    )

    member = modules.auth.register(
        username=f"member_{unique_id}",
        email=f"member_{unique_id}@example.com",
        password="TestPass123!",
    )

    non_member = modules.auth.register(
        username=f"nonmember_{unique_id}",
        email=f"nonmember_{unique_id}@example.com",
        password="TestPass123!",
    )

    server = modules.servers.create_server(owner.id, f"Test Server {unique_id}")
    modules.servers.add_member(server.id, member.id)

    channels = modules.servers.get_channels(owner.id, server.id)
    channel = channels[0] if channels else None

    return {
        "owner": owner,
        "member": member,
        "non_member": non_member,
        "server": server,
        "channel": channel,
        "auth": modules.auth,
        "messaging": modules.messaging,
        "servers": modules.servers,
        "embeds": modules.embeds,
        "webhooks": modules.webhooks,
        "db": modules._db,
    }


@pytest.fixture
def fresh_server(modules):
    """Create a fresh server for isolated tests."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"fresh_owner_{unique_id}",
        email=f"fresh_owner_{unique_id}@example.com",
        password="TestPass123!",
    )

    server = modules.servers.create_server(owner.id, f"Fresh Server {unique_id}")

    channels = modules.servers.get_channels(owner.id, server.id)
    channel = channels[0] if channels else None

    return {
        "owner": owner,
        "server": server,
        "channel": channel,
        "servers": modules.servers,
        "webhooks": modules.webhooks,
        "embeds": modules.embeds,
        "db": modules._db,
    }


@pytest.fixture
def webhook_with_token(base_server_setup):
    """Create a webhook and return it with token."""
    setup = base_server_setup
    unique_id = uuid.uuid4().hex[:8]

    webhook = setup["webhooks"].create_webhook(
        user_id=setup["owner"].id,
        channel_id=setup["channel"].id,
        name=f"Test Webhook {unique_id}",
    )

    return {
        **setup,
        "webhook": webhook,
        "token": webhook.token,
    }
