"""
Tests for voice module integration with other modules.
"""

import pytest


class TestServerIntegration:
    """Tests for integration with servers module."""

    def test_voice_channel_from_servers(self, server_with_voice):
        """Test voice channels created via servers module work."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        state = voice.join_channel(member1.id, voice_channel.id)

        assert state is not None
        assert state.server_id == server.id

    def test_stage_channel_from_servers(self, server_with_voice):
        """Test stage channels created via servers module work."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        state = voice.join_channel(member1.id, stage_channel.id)

        assert state is not None
        assert state.suppress is True

    def test_permission_check_uses_servers(self, server_with_voice):
        """Test permission checks use servers module."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        restricted_channel = servers.create_channel(
            owner.id, server.id, "restricted",
            channel_type=servers.ChannelType.VOICE
        )

        everyone_role = None
        roles = servers.get_roles(owner.id, server.id)
        for role in roles:
            if role.is_default:
                everyone_role = role
                break

        if everyone_role:
            servers.set_channel_override(
                owner.id, restricted_channel.id,
                "role", everyone_role.id,
                deny={"voice.connect": True}
            )

            with pytest.raises(voice.PermissionDeniedError):
                voice.join_channel(member1.id, restricted_channel.id)


class TestMultipleUsersInChannel:
    """Tests for multiple users in the same channel."""

    def test_multiple_users_join(self, server_with_voice):
        """Test multiple users can join the same channel."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, voice_channel.id)
        voice.join_channel(member1.id, voice_channel.id)
        voice.join_channel(member2.id, voice_channel.id)

        users = voice.get_channel_users(voice_channel.id)

        assert len(users) == 3

    def test_user_leave_doesnt_affect_others(self, server_with_voice):
        """Test one user leaving doesn't affect others."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, voice_channel.id)
        voice.join_channel(member1.id, voice_channel.id)

        voice.leave_channel(owner.id)

        state = voice.get_voice_state(member1.id)
        assert state is not None
        assert state.channel_id == voice_channel.id


class TestVoiceStateIsolation:
    """Tests for voice state isolation between users."""

    def test_self_mute_isolated(self, server_with_voice):
        """Test self-mute is isolated per user."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, voice_channel.id)
        voice.join_channel(member1.id, voice_channel.id)

        voice.set_self_mute(owner.id, True)

        owner_state = voice.get_voice_state(owner.id)
        member_state = voice.get_voice_state(member1.id)

        assert owner_state.self_mute is True
        assert member_state.self_mute is False

    def test_server_mute_isolated(self, server_with_moderator):
        """Test server mute is isolated per user."""
        owner, moderator, member, server, voice_channel, stage_channel, servers, voice = server_with_moderator

        voice.join_channel(moderator.id, voice_channel.id)
        voice.join_channel(member.id, voice_channel.id)

        voice.server_mute(owner.id, member.id, server.id)

        mod_state = voice.get_voice_state(moderator.id)
        member_state = voice.get_voice_state(member.id)

        assert mod_state.server_mute is False
        assert member_state.server_mute is True


class TestStageChannelWorkflow:
    """Tests for complete stage channel workflows."""

    def test_full_stage_workflow(self, server_with_voice):
        """Test a complete stage channel workflow."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, stage_channel.id)
        stage = voice.start_stage(owner.id, stage_channel.id, "Q&A Session")

        assert stage.topic == "Q&A Session"

        voice.join_channel(member1.id, stage_channel.id)
        voice.join_channel(member2.id, stage_channel.id)

        member1_state = voice.get_voice_state(member1.id)
        assert member1_state.suppress is True

        voice.request_to_speak(member1.id, stage_channel.id)

        requests = voice.get_speaker_requests(stage_channel.id)
        assert len(requests) == 1

        voice.invite_to_speak(owner.id, member1.id, stage_channel.id)

        member1_state = voice.get_voice_state(member1.id)
        assert member1_state.suppress is False

        requests = voice.get_speaker_requests(stage_channel.id)
        assert len(requests) == 0

        voice.move_to_audience(member1.id, member1.id, stage_channel.id)

        member1_state = voice.get_voice_state(member1.id)
        assert member1_state.suppress is True

        voice.end_stage(owner.id, stage_channel.id)

        stage = voice.get_stage(stage_channel.id)
        assert stage is None


class TestChannelUserCount:
    """Tests for channel user count tracking."""

    def test_user_count_increases_on_join(self, server_with_voice):
        """Test user count increases when users join."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        channel = voice.get_voice_channel(voice_channel.id, owner.id)
        initial_count = channel.user_count

        voice.join_channel(owner.id, voice_channel.id)

        channel = voice.get_voice_channel(voice_channel.id, owner.id)
        assert channel.user_count == initial_count + 1

    def test_user_count_decreases_on_leave(self, server_with_voice):
        """Test user count decreases when users leave."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.join_channel(owner.id, voice_channel.id)
        voice.join_channel(member1.id, voice_channel.id)

        channel = voice.get_voice_channel(voice_channel.id, owner.id)
        count_before = channel.user_count

        voice.leave_channel(owner.id)

        channel = voice.get_voice_channel(voice_channel.id, member1.id)
        assert channel.user_count == count_before - 1


class TestCrossServerIsolation:
    """Tests for isolation between servers."""

    def test_voice_state_isolated_per_server(self, db_and_modules):
        """Test voice states are isolated per server."""
        db, auth, servers, relationships, presence, voice = db_and_modules

        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"crossuser_{unique_id}",
            email=f"crossuser_{unique_id}@example.com",
            password="TestPass123!"
        )

        server1 = servers.create_server(user.id, f"Server 1 {unique_id}")
        server2 = servers.create_server(user.id, f"Server 2 {unique_id}")

        voice_channel1 = servers.create_channel(
            user.id, server1.id, "voice-1",
            channel_type=servers.ChannelType.VOICE
        )

        voice_channel2 = servers.create_channel(
            user.id, server2.id, "voice-2",
            channel_type=servers.ChannelType.VOICE
        )

        voice.join_channel(user.id, voice_channel1.id)

        state = voice.get_voice_state(user.id)
        assert state.server_id == server1.id

        voice.move_to_channel(user.id, voice_channel2.id)

        state = voice.get_voice_state(user.id)
        assert state.server_id == server2.id
