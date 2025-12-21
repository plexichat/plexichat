"""
Tests for voice channel join, leave, and move operations.
"""

import pytest


class TestJoinChannel:
    """Tests for joining voice channels."""

    def test_join_voice_channel(self, server_with_voice):
        """Test joining a voice channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        state = voice.join_channel(member1.id, voice_channel.id)

        assert state is not None
        assert state.user_id == member1.id
        assert state.channel_id == voice_channel.id
        assert state.server_id == server.id
        assert state.self_mute is False
        assert state.self_deaf is False
        assert state.server_mute is False
        assert state.server_deaf is False
        assert state.suppress is False
        assert state.joined_at > 0

    def test_join_stage_channel_as_audience(self, server_with_voice):
        """Test joining a stage channel starts as audience (suppressed)."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        state = voice.join_channel(member2.id, stage_channel.id)

        assert state is not None
        assert state.channel_id == stage_channel.id
        assert state.suppress is True

    def test_join_channel_already_in_same_channel(self, server_with_voice):
        """Test joining a channel user is already in raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, voice_channel.id)

        with pytest.raises(voice.UserAlreadyInChannelError):
            voice.join_channel(owner.id, voice_channel.id)

    def test_join_channel_leaves_previous(self, server_with_voice):
        """Test joining a new channel leaves the previous one."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice_channel2 = servers.create_channel(
            owner.id, server.id, "voice-2",
            channel_type=servers.ChannelType.VOICE
        )

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.join_channel(member1.id, voice_channel2.id)

        assert state.channel_id == voice_channel2.id

        users_in_original = voice.get_channel_users(voice_channel.id)
        assert not any(u.user_id == member1.id for u in users_in_original)

    def test_join_nonexistent_channel(self, server_with_voice):
        """Test joining a nonexistent channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.ChannelNotFoundError):
            voice.join_channel(member1.id, 999999999)

    def test_join_text_channel_fails(self, server_with_voice):
        """Test joining a text channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        text_channel = servers.create_channel(
            owner.id, server.id, "text-channel",
            channel_type=servers.ChannelType.TEXT
        )

        with pytest.raises(voice.ChannelTypeError):
            voice.join_channel(member1.id, text_channel.id)


class TestLeaveChannel:
    """Tests for leaving voice channels."""

    def test_leave_voice_channel(self, server_with_voice):
        """Test leaving a voice channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        result = voice.leave_channel(member1.id)

        assert result is True

        state = voice.get_voice_state(member1.id)
        assert state is None

    def test_leave_when_not_in_channel(self, server_with_voice):
        """Test leaving when not in a channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.UserNotInChannelError):
            voice.leave_channel(member2.id)

    def test_leave_clears_speaker_request(self, server_with_voice):
        """Test leaving a stage channel clears speaker request."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Stage")

        voice.join_channel(member1.id, stage_channel.id)
        voice.request_to_speak(member1.id, stage_channel.id)

        requests = voice.get_speaker_requests(stage_channel.id)
        assert len(requests) == 1

        voice.leave_channel(member1.id)

        requests = voice.get_speaker_requests(stage_channel.id)
        assert len(requests) == 0


class TestMoveToChannel:
    """Tests for moving between voice channels."""

    def test_move_to_different_channel(self, server_with_voice):
        """Test moving to a different voice channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice_channel2 = servers.create_channel(
            owner.id, server.id, "voice-move",
            channel_type=servers.ChannelType.VOICE
        )

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.move_to_channel(member1.id, voice_channel2.id)

        assert state.channel_id == voice_channel2.id

    def test_move_when_not_in_channel(self, server_with_voice):
        """Test moving when not in a channel joins the target."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        state = voice.move_to_channel(member2.id, voice_channel.id)

        assert state.channel_id == voice_channel.id


class TestGetChannelUsers:
    """Tests for getting users in a channel."""

    def test_get_channel_users(self, server_with_voice):
        """Test getting all users in a voice channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, voice_channel.id)
        voice.join_channel(member1.id, voice_channel.id)

        users = voice.get_channel_users(voice_channel.id)

        assert len(users) == 2
        user_ids = [u.user_id for u in users]
        assert owner.id in user_ids
        assert member1.id in user_ids

    def test_get_empty_channel_users(self, server_with_voice):
        """Test getting users from an empty channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice_channel2 = servers.create_channel(
            owner.id, server.id, "empty-voice",
            channel_type=servers.ChannelType.VOICE
        )

        users = voice.get_channel_users(voice_channel2.id)

        assert len(users) == 0


class TestGetVoiceChannels:
    """Tests for getting voice channels."""

    def test_get_voice_channels(self, server_with_voice):
        """Test getting all voice channels in a server."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        channels = voice.get_voice_channels(owner.id, server.id)

        assert len(channels) >= 2
        channel_ids = [c.id for c in channels]
        assert voice_channel.id in channel_ids
        assert stage_channel.id in channel_ids

    def test_get_voice_channel_info(self, server_with_voice):
        """Test getting voice channel info."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        channel = voice.get_voice_channel(voice_channel.id, owner.id)

        assert channel is not None
        assert channel.id == voice_channel.id
        assert channel.server_id == server.id
        assert channel.name == "voice-chat"
