"""
Tests for AFK channel functionality.
"""

import pytest
import time


class TestSetAFKChannel:
    """Tests for setting AFK channel."""

    def test_set_afk_channel(self, server_with_voice):
        """Test setting AFK channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        afk_channel = servers.create_channel(
            owner.id, server.id, "afk",
            channel_type=servers.ChannelType.VOICE
        )

        result = voice.set_afk_channel(owner.id, server.id, afk_channel.id, 300)

        assert result is True

        afk_id = voice.get_afk_channel(server.id)
        assert afk_id == afk_channel.id

    def test_set_afk_channel_disable(self, server_with_voice):
        """Test disabling AFK channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        afk_channel = servers.create_channel(
            owner.id, server.id, "afk-disable",
            channel_type=servers.ChannelType.VOICE
        )

        voice.set_afk_channel(owner.id, server.id, afk_channel.id, 300)
        result = voice.set_afk_channel(owner.id, server.id, None, 300)

        assert result is True

        afk_id = voice.get_afk_channel(server.id)
        assert afk_id is None

    def test_set_afk_channel_without_permission(self, server_with_voice):
        """Test setting AFK channel without permission fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.PermissionDeniedError):
            voice.set_afk_channel(member1.id, server.id, voice_channel.id, 300)

    def test_set_afk_channel_nonexistent(self, server_with_voice):
        """Test setting nonexistent AFK channel fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.ChannelNotFoundError):
            voice.set_afk_channel(owner.id, server.id, 999999999, 300)

    def test_set_afk_channel_text_channel_fails(self, server_with_voice):
        """Test setting text channel as AFK channel fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        text_channel = servers.create_channel(
            owner.id, server.id, "text-afk",
            channel_type=servers.ChannelType.TEXT
        )

        with pytest.raises(voice.ChannelTypeError):
            voice.set_afk_channel(owner.id, server.id, text_channel.id, 300)

    def test_set_afk_channel_minimum_timeout(self, server_with_voice):
        """Test AFK timeout has minimum of 60 seconds."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        afk_channel = servers.create_channel(
            owner.id, server.id, "afk-min",
            channel_type=servers.ChannelType.VOICE
        )

        result = voice.set_afk_channel(owner.id, server.id, afk_channel.id, 10)

        assert result is True


class TestGetAFKChannel:
    """Tests for getting AFK channel."""

    def test_get_afk_channel(self, server_with_voice):
        """Test getting AFK channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        afk_channel = servers.create_channel(
            owner.id, server.id, "afk-get",
            channel_type=servers.ChannelType.VOICE
        )

        voice.set_afk_channel(owner.id, server.id, afk_channel.id, 300)

        afk_id = voice.get_afk_channel(server.id)

        assert afk_id == afk_channel.id

    def test_get_afk_channel_not_set(self, server_with_voice):
        """Test getting AFK channel when not set returns None."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        new_server = servers.create_server(owner.id, "No AFK Server")

        afk_id = voice.get_afk_channel(new_server.id)

        assert afk_id is None


class TestCheckAFKTimeout:
    """Tests for AFK timeout checking."""

    def test_check_afk_timeout_no_afk_channel(self, server_with_voice):
        """Test AFK check with no AFK channel returns None."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)

        result = voice.check_afk_timeout(member1.id)

        assert result is None

    def test_check_afk_timeout_not_in_voice(self, server_with_voice):
        """Test AFK check when not in voice returns None."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        result = voice.check_afk_timeout(member1.id)

        assert result is None

    def test_check_afk_timeout_already_in_afk(self, server_with_voice):
        """Test AFK check when already in AFK channel returns None."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        afk_channel = servers.create_channel(
            owner.id, server.id, "afk-already",
            channel_type=servers.ChannelType.VOICE
        )

        voice.set_afk_channel(owner.id, server.id, afk_channel.id, 1)

        voice.join_channel(member1.id, afk_channel.id)

        result = voice.check_afk_timeout(member1.id)

        assert result is None

    def test_check_afk_timeout_not_expired(self, server_with_voice):
        """Test AFK check when timeout not expired returns None."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        afk_channel = servers.create_channel(
            owner.id, server.id, "afk-notexp",
            channel_type=servers.ChannelType.VOICE
        )

        voice.set_afk_channel(owner.id, server.id, afk_channel.id, 3600)

        voice.join_channel(member1.id, voice_channel.id)

        result = voice.check_afk_timeout(member1.id)

        assert result is None

    def test_check_afk_timeout_moves_user(self, db_and_modules):
        """Test AFK check moves user when timeout expired."""
        db, auth, servers, relationships, presence, voice = db_and_modules

        import uuid
        unique_id = uuid.uuid4().hex[:8]

        owner = auth.register(
            username=f"afkowner_{unique_id}",
            email=f"afkowner_{unique_id}@example.com",
            password="TestPass123!"
        )

        member = auth.register(
            username=f"afkmember_{unique_id}",
            email=f"afkmember_{unique_id}@example.com",
            password="TestPass123!"
        )

        server = servers.create_server(owner.id, f"AFK Test Server {unique_id}")
        servers.add_member(server.id, member.id)

        voice_channel = servers.create_channel(
            owner.id, server.id, "voice-afk-test",
            channel_type=servers.ChannelType.VOICE
        )

        afk_channel = servers.create_channel(
            owner.id, server.id, "afk-test",
            channel_type=servers.ChannelType.VOICE
        )

        voice.set_afk_channel(owner.id, server.id, afk_channel.id, 60)

        voice.join_channel(member.id, voice_channel.id)

        db.execute(
            "UPDATE voice_states SET last_activity = ? WHERE user_id = ?",
            (int(time.time() * 1000) - 120000, member.id)
        )

        result = voice.check_afk_timeout(member.id)

        assert result is not None
        assert result.channel_id == afk_channel.id
