"""
Tests for edge cases and error handling.
"""

import pytest


class TestChannelNotFound:
    """Tests for channel not found scenarios."""

    def test_join_deleted_channel(self, server_with_voice):
        """Test joining a deleted channel fails."""
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

        temp_channel = servers.create_channel(
            owner.id, server.id, "temp-voice", channel_type=servers.ChannelType.VOICE
        )

        servers.delete_channel(owner.id, temp_channel.id)

        with pytest.raises(voice.ChannelNotFoundError):
            voice.join_channel(member1.id, temp_channel.id)

    def test_get_voice_channel_nonexistent(self, server_with_voice):
        """Test getting nonexistent voice channel returns None."""
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

        channel = voice.get_voice_channel(999999999, owner.id)

        assert channel is None


class TestUserNotInChannel:
    """Tests for user not in channel scenarios."""

    def test_leave_when_not_in_channel(self, server_with_voice):
        """Test leaving when not in channel raises error."""
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

        with pytest.raises(voice.UserNotInChannelError):
            voice.leave_channel(member1.id)

    def test_set_self_mute_when_not_in_channel(self, server_with_voice):
        """Test setting self-mute when not in channel raises error."""
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

        with pytest.raises(voice.UserNotInChannelError):
            voice.set_self_mute(member1.id, True)

    def test_server_mute_user_not_in_channel(self, server_with_moderator):
        """Test server muting user not in channel raises error."""
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


class TestChannelTypeMismatch:
    """Tests for channel type mismatch scenarios."""

    def test_join_text_channel(self, server_with_voice):
        """Test joining a text channel fails."""
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

        text_channel = servers.create_channel(
            owner.id, server.id, "text-only", channel_type=servers.ChannelType.TEXT
        )

        with pytest.raises(voice.ChannelTypeError) as exc_info:
            voice.join_channel(member1.id, text_channel.id)

        assert exc_info.value.expected == "voice or stage"
        assert exc_info.value.actual == "text"

    def test_start_stage_on_voice_channel(self, server_with_voice):
        """Test starting stage on voice channel fails."""
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

        voice.join_channel(owner.id, voice_channel.id)

        with pytest.raises(voice.ChannelTypeError) as exc_info:
            voice.start_stage(owner.id, voice_channel.id, "Test")

        assert exc_info.value.expected == "stage"


class TestStageErrors:
    """Tests for stage-related error scenarios."""

    def test_request_to_speak_no_stage(self, server_with_voice):
        """Test requesting to speak with no active stage fails."""
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

        voice.join_channel(member1.id, stage_channel.id)

        with pytest.raises(voice.StageNotFoundError):
            voice.request_to_speak(member1.id, stage_channel.id)

    def test_end_stage_no_stage(self, server_with_voice):
        """Test ending stage when none active fails."""
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

        with pytest.raises(voice.StageNotFoundError):
            voice.end_stage(owner.id, stage_channel.id)

    def test_invite_to_speak_already_speaker(self, server_with_voice):
        """Test inviting already speaker fails."""
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

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test")

        voice.join_channel(member1.id, stage_channel.id)
        voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

        with pytest.raises(voice.AlreadySpeakerError):
            voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

    def test_move_to_audience_not_speaker(self, server_with_voice):
        """Test moving non-speaker to audience fails."""
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

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test")

        voice.join_channel(member1.id, stage_channel.id)

        with pytest.raises(voice.NotSpeakerError):
            voice.move_to_audience(owner.id, member1.id, stage_channel.id)


class TestSpeakerRequestErrors:
    """Tests for speaker request error scenarios."""

    def test_duplicate_speaker_request(self, server_with_voice):
        """Test duplicate speaker request fails."""
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

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test")

        voice.join_channel(member1.id, stage_channel.id)
        voice.request_to_speak(member1.id, stage_channel.id)

        with pytest.raises(voice.SpeakerRequestExistsError):
            voice.request_to_speak(member1.id, stage_channel.id)

    def test_cancel_nonexistent_request(self, server_with_voice):
        """Test canceling nonexistent request fails."""
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

        with pytest.raises(voice.SpeakerRequestNotFoundError):
            voice.cancel_speak_request(member1.id, stage_channel.id)


class TestServerMismatch:
    """Tests for server mismatch scenarios."""

    def test_server_mute_wrong_server(self, db_and_modules):
        """Test server muting user in different server fails."""
        db, auth, servers, relationships, presence, voice = db_and_modules

        import uuid

        unique_id = uuid.uuid4().hex[:8]

        owner = auth.register(
            username=f"mismatch_owner_{unique_id}",
            email=f"mismatch_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        member = auth.register(
            username=f"mismatch_member_{unique_id}",
            email=f"mismatch_member_{unique_id}@example.com",
            password="TestPass123!",
        )

        server1 = servers.create_server(owner.id, f"Server 1 {unique_id}")
        server2 = servers.create_server(owner.id, f"Server 2 {unique_id}")

        servers.add_member(server1.id, member.id)
        servers.add_member(server2.id, member.id)

        voice_channel = servers.create_channel(
            owner.id,
            server1.id,
            "voice-mismatch",
            channel_type=servers.ChannelType.VOICE,
        )

        voice.join_channel(member.id, voice_channel.id)

        with pytest.raises(voice.ChannelAccessDeniedError):
            voice.server_mute(owner.id, member.id, server2.id)


class TestChannelFullErrors:
    """Tests for channel full error scenarios."""

    def test_channel_full_error_details(self, server_with_voice):
        """Test channel full error contains correct details."""
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

        voice.set_user_limit(owner.id, voice_channel.id, 1)
        voice.join_channel(owner.id, voice_channel.id)

        with pytest.raises(voice.ChannelFullError) as exc_info:
            voice.join_channel(member1.id, voice_channel.id)

        assert exc_info.value.limit == 1
        assert exc_info.value.current == 1


class TestVoiceStateConsistency:
    """Tests for voice state consistency."""

    def test_leave_clears_all_state(self, server_with_voice):
        """Test leaving clears all voice state."""
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

        voice.join_channel(member1.id, voice_channel.id)
        voice.set_self_mute(member1.id, True)
        voice.set_streaming(member1.id, True)

        voice.leave_channel(member1.id)

        state = voice.get_voice_state(member1.id)
        assert state is None

    def test_move_preserves_self_state(self, server_with_voice):
        """Test moving preserves self-mute/deaf state."""
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
            owner.id,
            server.id,
            "voice-preserve",
            channel_type=servers.ChannelType.VOICE,
        )

        voice.join_channel(member1.id, voice_channel.id)
        voice.set_self_mute(member1.id, True)

        voice.move_to_channel(member1.id, voice_channel2.id)

        state = voice.get_voice_state(member1.id)
        assert state.channel_id == voice_channel2.id


class TestEmptyResults:
    """Tests for empty result scenarios."""

    def test_get_channel_users_empty(self, server_with_voice):
        """Test getting users from empty channel returns empty list."""
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

        users = voice.get_channel_users(voice_channel.id)

        assert users == []

    def test_get_speaker_requests_empty(self, server_with_voice):
        """Test getting speaker requests when none exist returns empty list."""
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

        requests = voice.get_speaker_requests(stage_channel.id)

        assert requests == []

    def test_get_speakers_empty(self, server_with_voice):
        """Test getting speakers when none exist returns empty list."""
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

        speakers = voice.get_speakers(stage_channel.id)

        assert speakers == []

    def test_get_audience_empty(self, server_with_voice):
        """Test getting audience when none exist returns empty list."""
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

        audience = voice.get_audience(stage_channel.id)

        assert audience == []
