"""
Shared fixtures for sticker tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def db_and_modules(
    db, auth_manager, messaging_manager, server_manager, sticker_manager
):
    """Override root ``db_and_modules`` for the stickers tree.

    The root ``db_and_modules`` fixture returns
    ``(db, auth, messaging, servers, embeds_manager)`` so unpacking
    ``db, auth, messaging, servers, stickers`` ends up binding
    ``stickers = embeds_manager`` — which then surfaces as
    ``AttributeError: 'EmbedManager' object has no attribute
    'create_pack'`` (and friends) inside sticker tests.

    This override keeps the 5-tuple positional contract the sticker
    tests expect but swaps the 5th element to ``sticker_manager``
    (already wired with ``servers_module`` and ``messaging_module``
    by the root fixture).
    """
    return (
        db,
        auth_manager,
        messaging_manager,
        server_manager,
        sticker_manager,
    )


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
