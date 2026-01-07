"""
Shared fixtures for voice tests.

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
        modules.voice,
    )


@pytest.fixture
def users(modules, user_pool):
    """Get test users."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()
    user3 = user_pool.get_user()
    user4 = user_pool.get_user()
    return user1, user2, user3, user4, modules.voice


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

    return user1, user2, modules.voice


@pytest.fixture
def server_with_voice(modules):
    """Create a server with voice channels."""
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

    server = modules.servers.create_server(owner.id, f"Voice Test Server {unique_id}")
    modules.servers.add_member(server.id, member1.id)
    modules.servers.add_member(server.id, member2.id)

    voice_channel = modules.servers.create_channel(
        owner.id,
        server.id,
        "voice-chat",
        channel_type=modules.servers.ChannelType.VOICE,
    )

    stage_channel = modules.servers.create_channel(
        owner.id,
        server.id,
        "stage-talk",
        channel_type=modules.servers.ChannelType.STAGE,
    )

    return (
        owner,
        member1,
        member2,
        server,
        voice_channel,
        stage_channel,
        modules.servers,
        modules.voice,
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
        permissions={
            "voice.mute_members": True,
            "voice.deafen_members": True,
            "voice.move_members": True,
        },
    )
    modules.servers.assign_role(owner.id, server.id, moderator.id, mod_role.id)

    voice_channel = modules.servers.create_channel(
        owner.id, server.id, "mod-voice", channel_type=modules.servers.ChannelType.VOICE
    )

    stage_channel = modules.servers.create_channel(
        owner.id, server.id, "mod-stage", channel_type=modules.servers.ChannelType.STAGE
    )

    return (
        owner,
        moderator,
        member,
        server,
        voice_channel,
        stage_channel,
        modules.servers,
        modules.voice,
    )
