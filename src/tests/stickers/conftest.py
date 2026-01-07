"""
Shared fixtures for sticker tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def server_with_owner(modules, user_pool):
    """Create a server with owner for sticker tests."""
    owner = user_pool.get_user()

    unique_id = uuid.uuid4().hex[:6]
    server = modules.servers.create_server(owner.id, f"Test Server {unique_id}")

    return owner, server, modules.stickers, modules.servers


@pytest.fixture
def server_with_pack(server_with_owner):
    """Create a server with a sticker pack."""
    owner, server, stickers, servers = server_with_owner

    pack = stickers.create_pack(
        user_id=owner.id,
        name="Test Pack",
        description="Test sticker pack",
        server_id=server.id,
    )

    return owner, server, pack, stickers, servers
