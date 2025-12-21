"""
Tests for voice state operations (mute, deaf, streaming, video).
"""

import pytest


class TestSelfMute:
    """Tests for self-mute functionality."""

    def test_set_self_mute(self, server_with_voice):
        """Test setting self-mute."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.set_self_mute(member1.id, True)

        assert state.self_mute is True

    def test_unset_self_mute(self, server_with_voice):
        """Test unsetting self-mute."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        voice.set_self_mute(member1.id, True)
        state = voice.set_self_mute(member1.id, False)

        assert state.self_mute is False

    def test_self_mute_not_in_channel(self, server_with_voice):
        """Test self-mute when not in channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.UserNotInChannelError):
            voice.set_self_mute(member2.id, True)


class TestSelfDeaf:
    """Tests for self-deaf functionality."""

    def test_set_self_deaf(self, server_with_voice):
        """Test setting self-deaf."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.set_self_deaf(member1.id, True)

        assert state.self_deaf is True
        assert state.self_mute is True

    def test_unset_self_deaf(self, server_with_voice):
        """Test unsetting self-deaf."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        voice.set_self_deaf(member1.id, True)
        state = voice.set_self_deaf(member1.id, False)

        assert state.self_deaf is False

    def test_self_deaf_not_in_channel(self, server_with_voice):
        """Test self-deaf when not in channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.UserNotInChannelError):
            voice.set_self_deaf(member2.id, True)


class TestStreaming:
    """Tests for streaming (screen share) functionality."""

    def test_set_streaming(self, server_with_voice):
        """Test setting streaming state."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.set_streaming(member1.id, True)

        assert state.streaming is True

    def test_unset_streaming(self, server_with_voice):
        """Test unsetting streaming state."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        voice.set_streaming(member1.id, True)
        state = voice.set_streaming(member1.id, False)

        assert state.streaming is False

    def test_streaming_not_in_channel(self, server_with_voice):
        """Test streaming when not in channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.UserNotInChannelError):
            voice.set_streaming(member2.id, True)


class TestVideo:
    """Tests for video (camera) functionality."""

    def test_set_video(self, server_with_voice):
        """Test setting video state."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.set_video(member1.id, True)

        assert state.video is True

    def test_unset_video(self, server_with_voice):
        """Test unsetting video state."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        voice.set_video(member1.id, True)
        state = voice.set_video(member1.id, False)

        assert state.video is False

    def test_video_not_in_channel(self, server_with_voice):
        """Test video when not in channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.UserNotInChannelError):
            voice.set_video(member2.id, True)


class TestUpdateVoiceState:
    """Tests for updating multiple voice state properties."""

    def test_update_multiple_states(self, server_with_voice):
        """Test updating multiple voice state properties at once."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.update_voice_state(
            member1.id,
            self_mute=True,
            streaming=True,
            video=True
        )

        assert state.self_mute is True
        assert state.streaming is True
        assert state.video is True

    def test_update_self_deaf_also_mutes(self, server_with_voice):
        """Test that setting self_deaf also sets self_mute."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.update_voice_state(member1.id, self_deaf=True)

        assert state.self_deaf is True
        assert state.self_mute is True

    def test_update_no_changes(self, server_with_voice):
        """Test updating with no changes returns current state."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.update_voice_state(member1.id)

        assert state is not None
        assert state.user_id == member1.id

    def test_update_not_in_channel(self, server_with_voice):
        """Test updating when not in channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.UserNotInChannelError):
            voice.update_voice_state(member2.id, self_mute=True)


class TestGetVoiceState:
    """Tests for getting voice state."""

    def test_get_voice_state(self, server_with_voice):
        """Test getting voice state."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.get_voice_state(member1.id)

        assert state is not None
        assert state.user_id == member1.id
        assert state.channel_id == voice_channel.id

    def test_get_voice_state_not_in_channel(self, server_with_voice):
        """Test getting voice state when not in channel returns None."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        state = voice.get_voice_state(member2.id)

        assert state is None


class TestIsUserInVoice:
    """Tests for checking if user is in voice."""

    def test_is_user_in_voice_true(self, server_with_voice):
        """Test is_user_in_voice returns True when in channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, voice_channel.id)
        result = voice.is_user_in_voice(member1.id)

        assert result is True

    def test_is_user_in_voice_false(self, server_with_voice):
        """Test is_user_in_voice returns False when not in channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        result = voice.is_user_in_voice(member2.id)

        assert result is False
