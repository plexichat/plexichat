"""
Tests for stage channel functionality.
"""

import pytest


class TestStartStage:
    """Tests for starting stage instances."""

    def test_start_stage(self, server_with_voice):
        """Test starting a stage instance."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        stage = voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        assert stage is not None
        assert stage.channel_id == stage_channel.id
        assert stage.topic == "Test Topic"
        assert stage.started_by == owner.id
        assert stage.started_at > 0

    def test_start_stage_makes_starter_speaker(self, server_with_voice):
        """Test starting a stage makes the starter a speaker."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        state = voice.get_voice_state(owner.id)
        assert state.suppress is False

    def test_start_stage_on_voice_channel_fails(self, server_with_voice):
        """Test starting a stage on a voice channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, voice_channel.id)

        with pytest.raises(voice.ChannelTypeError):
            voice.start_stage(owner.id, voice_channel.id, "Test Topic")

    def test_start_stage_already_active(self, server_with_voice):
        """Test starting a stage when one is already active raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "First Stage")

        with pytest.raises(voice.VoiceError):
            voice.start_stage(owner.id, stage_channel.id, "Second Stage")


class TestEndStage:
    """Tests for ending stage instances."""

    def test_end_stage_by_starter(self, server_with_voice):
        """Test ending a stage by the starter."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        result = voice.end_stage(owner.id, stage_channel.id)

        assert result is True

        stage = voice.get_stage(stage_channel.id)
        assert stage is None

    def test_end_stage_suppresses_all(self, server_with_voice):
        """Test ending a stage suppresses all users."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

        voice.end_stage(owner.id, stage_channel.id)

        state = voice.get_voice_state(member1.id)
        assert state.suppress is True

    def test_end_stage_clears_requests(self, server_with_voice):
        """Test ending a stage clears speaker requests."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.request_to_speak(member1.id, stage_channel.id)

        voice.end_stage(owner.id, stage_channel.id)

        requests = voice.get_speaker_requests(stage_channel.id)
        assert len(requests) == 0

    def test_end_stage_no_active_stage(self, server_with_voice):
        """Test ending a stage when none is active raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.StageNotFoundError):
            voice.end_stage(owner.id, stage_channel.id)


class TestRequestToSpeak:
    """Tests for requesting to speak (raise hand)."""

    def test_request_to_speak(self, server_with_voice):
        """Test requesting to speak."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        request = voice.request_to_speak(member1.id, stage_channel.id)

        assert request is not None
        assert request.user_id == member1.id
        assert request.channel_id == stage_channel.id

    def test_request_to_speak_already_speaker(self, server_with_voice):
        """Test requesting to speak when already a speaker raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        with pytest.raises(voice.AlreadySpeakerError):
            voice.request_to_speak(owner.id, stage_channel.id)

    def test_request_to_speak_duplicate(self, server_with_voice):
        """Test duplicate request to speak raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.request_to_speak(member1.id, stage_channel.id)

        with pytest.raises(voice.SpeakerRequestExistsError):
            voice.request_to_speak(member1.id, stage_channel.id)

    def test_request_to_speak_not_in_channel(self, server_with_voice):
        """Test requesting to speak when not in channel raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        with pytest.raises(voice.UserNotInChannelError):
            voice.request_to_speak(member1.id, stage_channel.id)

    def test_request_to_speak_no_stage(self, server_with_voice):
        """Test requesting to speak with no active stage raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(member1.id, stage_channel.id)

        with pytest.raises(voice.StageNotFoundError):
            voice.request_to_speak(member1.id, stage_channel.id)


class TestCancelSpeakRequest:
    """Tests for canceling speak requests."""

    def test_cancel_speak_request(self, server_with_voice):
        """Test canceling a speak request."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.request_to_speak(member1.id, stage_channel.id)

        result = voice.cancel_speak_request(member1.id, stage_channel.id)

        assert result is True

        requests = voice.get_speaker_requests(stage_channel.id)
        assert len(requests) == 0

    def test_cancel_speak_request_not_found(self, server_with_voice):
        """Test canceling a nonexistent speak request raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.SpeakerRequestNotFoundError):
            voice.cancel_speak_request(member1.id, stage_channel.id)


class TestInviteToSpeak:
    """Tests for inviting users to speak."""

    def test_invite_to_speak(self, server_with_voice):
        """Test inviting a user to speak."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        state = voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

        assert state.suppress is False

    def test_invite_to_speak_clears_request(self, server_with_voice):
        """Test inviting to speak clears pending request."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.request_to_speak(member1.id, stage_channel.id)

        voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

        requests = voice.get_speaker_requests(stage_channel.id)
        assert len(requests) == 0

    def test_invite_to_speak_already_speaker(self, server_with_voice):
        """Test inviting an already speaker raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

        with pytest.raises(voice.AlreadySpeakerError):
            voice.invite_to_speak(owner.id, member1.id, stage_channel.id)


class TestMoveToAudience:
    """Tests for moving speakers to audience."""

    def test_move_to_audience(self, server_with_voice):
        """Test moving a speaker to audience."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

        state = voice.move_to_audience(owner.id, member1.id, stage_channel.id)

        assert state.suppress is True

    def test_move_self_to_audience(self, server_with_voice):
        """Test moving self to audience."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

        state = voice.move_to_audience(member1.id, member1.id, stage_channel.id)

        assert state.suppress is True

    def test_move_to_audience_not_speaker(self, server_with_voice):
        """Test moving non-speaker to audience raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)

        with pytest.raises(voice.NotSpeakerError):
            voice.move_to_audience(owner.id, member1.id, stage_channel.id)


class TestGetSpeakersAndAudience:
    """Tests for getting speakers and audience."""

    def test_get_speakers(self, server_with_voice):
        """Test getting speakers in a stage channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

        speakers = voice.get_speakers(stage_channel.id)

        assert len(speakers) == 2
        speaker_ids = [s.user_id for s in speakers]
        assert owner.id in speaker_ids
        assert member1.id in speaker_ids

    def test_get_audience(self, server_with_voice):
        """Test getting audience in a stage channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.join_channel(member2.id, stage_channel.id)

        audience = voice.get_audience(stage_channel.id)

        assert len(audience) == 2
        audience_ids = [a.user_id for a in audience]
        assert member1.id in audience_ids
        assert member2.id in audience_ids

    def test_get_speaker_requests(self, server_with_voice):
        """Test getting speaker requests."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Topic")

        voice.join_channel(member1.id, stage_channel.id)
        voice.join_channel(member2.id, stage_channel.id)

        voice.request_to_speak(member1.id, stage_channel.id)
        voice.request_to_speak(member2.id, stage_channel.id)

        requests = voice.get_speaker_requests(stage_channel.id)

        assert len(requests) == 2
