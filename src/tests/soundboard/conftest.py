"""
Shared fixtures for soundboard tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest
import uuid


@pytest.fixture
def server_with_owner(modules, user_pool):
    """Create a server with owner for soundboard tests."""
    owner = user_pool.get_user()

    unique_id = uuid.uuid4().hex[:6]
    server = modules.servers.create_server(owner.id, f"Soundboard Server {unique_id}")

    return owner, server, modules.soundboard, modules.servers


@pytest.fixture
def server_with_sound(server_with_owner):
    """Create a server with a sound."""
    owner, server, soundboard, servers = server_with_owner
    from src.core.soundboard import SoundFormat

    sound = soundboard.upload_sound(
        user_id=owner.id,
        server_id=server.id,
        name="test_sound",
        format=SoundFormat.MP3,
        url="https://cdn.example.com/sounds/test.mp3",
        size=100000,
        duration_seconds=2.5
    )

    return owner, server, sound, soundboard, servers
