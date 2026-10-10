"""
Tests for server moderation actions (server mute/deaf, move, disconnect).
"""

from unittest.mock import patch

import pytest


class _FakeCall:
    def __init__(
        self,
        call_id,
        channel_id,
        server_id=None,
        initiator_id=None,
        conversation_id=None,
    ):
        self.id = call_id
        self.channel_id = channel_id
        self.server_id = server_id
        self.initiator_id = initiator_id
        self.conversation_id = conversation_id
        self.duration_seconds = 1


class _FakeCallManager:
    """Minimal VoiceCallManager surface used by the call-lifecycle hooks."""

    def __init__(self):
        self._active = {}
        self.participants = {}

    def get_active_by_channel(self, channel_id):
        return self._active.get(channel_id)

    def start_call(self, **kwargs):
        call = _FakeCall(call_id=kwargs["channel_id"], **kwargs)
        self._active[call.channel_id] = call
        self.participants.setdefault(call.id, set()).add(kwargs["initiator_id"])
        return call

    def add_participant(self, call_id, user_id):
        self.participants.setdefault(call_id, set()).add(user_id)

    def remove_participant(self, call_id, user_id):
        self.participants.setdefault(call_id, set()).discard(user_id)

    def end_call(self, call_id):
        return self._active.pop(call_id, None)


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

    def test_move_member_fires_call_lifecycle_hooks(self, server_with_moderator):
        """A moderation move is a leave+join and must run both hooks."""
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
            owner.id,
            server.id,
            "voice-move-hooks",
            channel_type=servers.ChannelType.VOICE,
        )

        voice.join_channel(member.id, voice_channel.id)

        with (
            patch.object(voice, "_on_member_left") as left,
            patch.object(voice, "_on_member_joined") as joined,
        ):
            voice.move_member(moderator.id, member.id, voice_channel2.id)

        left.assert_called_once_with(member.id, voice_channel.id)
        joined.assert_called_once_with(member.id, voice_channel2.id)

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

    def test_disconnect_member_fires_member_left_hook(self, server_with_moderator):
        """A moderation disconnect must run the call-lifecycle leave hook.

        ``leave_channel`` ends an emptied channel's call and prunes the
        participant snapshot through ``_on_member_left``; the moderation path
        skipped it, so the call stayed active for an empty channel and a
        rejoin bypassed the recording-consent gate.
        """
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

        with patch.object(voice, "_on_member_left") as left:
            voice.disconnect_member(moderator.id, member.id, server.id)

        left.assert_called_once_with(member.id, voice_channel.id)

    def test_disconnect_member_ends_empty_channel_call(self, server_with_moderator):
        """The leave hook must actually end the call once the channel empties."""
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

        calls = _FakeCallManager()
        voice.set_voice_call_manager(calls)

        voice.join_channel(member.id, voice_channel.id)
        assert calls.get_active_by_channel(voice_channel.id) is not None

        voice.disconnect_member(moderator.id, member.id, server.id)

        assert calls.get_active_by_channel(voice_channel.id) is None

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
