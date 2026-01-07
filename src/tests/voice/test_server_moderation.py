"""
Tests for server moderation actions (server mute/deaf, move, disconnect).
"""

import pytest


class TestServerMute:
    """Tests for server mute functionality."""

    def test_server_mute_member(self, server_with_moderator):
        """Test server muting a member."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        voice.join_channel(member.id, voice_channel.id)
        state = voice.server_mute(moderator.id, member.id, server.id)

        assert state.server_mute is True

    def test_server_mute_by_owner(self, server_with_moderator):
        """Test server muting by owner."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        voice.join_channel(member.id, voice_channel.id)
        state = voice.server_mute(owner.id, member.id, server.id)

        assert state.server_mute is True

    def test_server_mute_without_permission(self, server_with_voice):
        """Test server mute without permission raises error."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        voice.join_channel(member2.id, voice_channel.id)

        with pytest.raises(voice.PermissionDeniedError):
            voice.server_mute(member1.id, member2.id, server.id)

    def test_server_mute_user_not_in_channel(self, server_with_moderator):
        """Test server mute when user not in channel raises error."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        with pytest.raises(voice.UserNotInChannelError):
            voice.server_mute(moderator.id, member.id, server.id)


class TestServerUnmute:
    """Tests for server unmute functionality."""

    def test_server_unmute_member(self, server_with_moderator):
        """Test server unmuting a member."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        voice.join_channel(member.id, voice_channel.id)
        voice.server_mute(moderator.id, member.id, server.id)
        state = voice.server_unmute(moderator.id, member.id, server.id)

        assert state.server_mute is False


class TestServerDeaf:
    """Tests for server deaf functionality."""

    def test_server_deaf_member(self, server_with_moderator):
        """Test server deafening a member."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        voice.join_channel(member.id, voice_channel.id)
        state = voice.server_deaf(moderator.id, member.id, server.id)

        assert state.server_deaf is True
        assert state.server_mute is True

    def test_server_deaf_without_permission(self, server_with_voice):
        """Test server deaf without permission raises error."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        voice.join_channel(member2.id, voice_channel.id)

        with pytest.raises(voice.PermissionDeniedError):
            voice.server_deaf(member1.id, member2.id, server.id)


class TestServerUndeaf:
    """Tests for server undeaf functionality."""

    def test_server_undeaf_member(self, server_with_moderator):
        """Test server undeafening a member."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        voice.join_channel(member.id, voice_channel.id)
        voice.server_deaf(moderator.id, member.id, server.id)
        state = voice.server_undeaf(moderator.id, member.id, server.id)

        assert state.server_deaf is False


class TestMoveMember:
    """Tests for moving members between channels."""

    def test_move_member_to_channel(self, server_with_moderator):
        """Test moving a member to a different channel."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        voice_channel2 = servers.create_channel(
            owner.id, server.id, "voice-2", channel_type=servers.ChannelType.VOICE
        )

        voice.join_channel(member.id, voice_channel.id)
        state = voice.move_member(moderator.id, member.id, voice_channel2.id)

        assert state.channel_id == voice_channel2.id

    def test_move_member_without_permission(self, server_with_voice):
        """Test moving member without permission raises error."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        voice_channel2 = servers.create_channel(
            owner.id, server.id, "voice-move2", channel_type=servers.ChannelType.VOICE
        )

        voice.join_channel(member2.id, voice_channel.id)

        with pytest.raises(voice.PermissionDeniedError):
            voice.move_member(member1.id, member2.id, voice_channel2.id)

    def test_move_member_not_in_channel(self, server_with_moderator):
        """Test moving member not in channel raises error."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        with pytest.raises(voice.UserNotInChannelError):
            voice.move_member(moderator.id, member.id, voice_channel.id)

    def test_move_member_to_nonexistent_channel(self, server_with_moderator):
        """Test moving member to nonexistent channel raises error."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        voice.join_channel(member.id, voice_channel.id)

        with pytest.raises(voice.ChannelNotFoundError):
            voice.move_member(moderator.id, member.id, 999999999)

    def test_move_member_to_text_channel(self, server_with_moderator):
        """Test moving member to text channel raises error."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        text_channel = servers.create_channel(
            owner.id, server.id, "text-move", channel_type=servers.ChannelType.TEXT
        )

        voice.join_channel(member.id, voice_channel.id)

        with pytest.raises(voice.ChannelTypeError):
            voice.move_member(moderator.id, member.id, text_channel.id)


class TestDisconnectMember:
    """Tests for disconnecting members from voice."""

    def test_disconnect_member(self, server_with_moderator):
        """Test disconnecting a member from voice."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        voice.join_channel(member.id, voice_channel.id)
        result = voice.disconnect_member(moderator.id, member.id, server.id)

        assert result is True

        state = voice.get_voice_state(member.id)
        assert state is None

    def test_disconnect_member_without_permission(self, server_with_voice):
        """Test disconnecting member without permission raises error."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        voice.join_channel(member2.id, voice_channel.id)

        with pytest.raises(voice.PermissionDeniedError):
            voice.disconnect_member(member1.id, member2.id, server.id)

    def test_disconnect_member_not_in_channel(self, server_with_moderator):
        """Test disconnecting member not in channel raises error."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        with pytest.raises(voice.UserNotInChannelError):
            voice.disconnect_member(moderator.id, member.id, server.id)

    def test_disconnect_clears_speaker_request(self, server_with_moderator):
        """Test disconnecting clears speaker requests."""
        (
            owner,
            moderator,
            member,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_moderator

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Stage")

        voice.join_channel(member.id, stage_channel.id)
        voice.request_to_speak(member.id, stage_channel.id)

        voice.disconnect_member(moderator.id, member.id, server.id)

        requests = voice.get_speaker_requests(stage_channel.id)
        assert len(requests) == 0
