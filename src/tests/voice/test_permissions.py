"""
Tests for voice permission checks.
"""

import pytest


class TestConnectPermission:
    """Tests for voice.connect permission."""

    def test_join_with_connect_permission(self, server_with_voice):
        """Test joining with connect permission succeeds."""
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

        state = voice.join_channel(member1.id, voice_channel.id)

        assert state is not None
        assert state.channel_id == voice_channel.id

    def test_join_without_connect_permission(self, server_with_voice):
        """Test joining without connect permission fails."""
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

        restricted_channel = servers.create_channel(
            owner.id,
            server.id,
            "restricted-voice",
            channel_type=servers.ChannelType.VOICE,
        )

        everyone_role = None
        roles = servers.get_roles(owner.id, server.id)
        for role in roles:
            if role.is_default:
                everyone_role = role
                break

        if everyone_role:
            servers.set_channel_override(
                owner.id,
                restricted_channel.id,
                "role",
                everyone_role.id,
                deny={"voice.connect": True},
            )

            with pytest.raises(voice.PermissionDeniedError) as exc_info:
                voice.join_channel(member1.id, restricted_channel.id)

            assert exc_info.value.permission == "voice.connect"


class TestMutePermission:
    """Tests for voice.mute_members permission."""

    def test_server_mute_with_permission(self, server_with_moderator):
        """Test server mute with permission succeeds."""
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

    def test_server_mute_without_permission(self, server_with_voice):
        """Test server mute without permission fails."""
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

        with pytest.raises(voice.PermissionDeniedError) as exc_info:
            voice.server_mute(member1.id, member2.id, server.id)

        assert exc_info.value.permission == "voice.mute_members"


class TestDeafenPermission:
    """Tests for voice.deafen_members permission."""

    def test_server_deaf_with_permission(self, server_with_moderator):
        """Test server deaf with permission succeeds."""
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

    def test_server_deaf_without_permission(self, server_with_voice):
        """Test server deaf without permission fails."""
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

        with pytest.raises(voice.PermissionDeniedError) as exc_info:
            voice.server_deaf(member1.id, member2.id, server.id)

        assert exc_info.value.permission == "voice.deafen_members"


class TestMovePermission:
    """Tests for voice.move_members permission."""

    def test_move_member_with_permission(self, server_with_moderator):
        """Test moving member with permission succeeds."""
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
            owner.id, server.id, "voice-perm", channel_type=servers.ChannelType.VOICE
        )

        voice.join_channel(member.id, voice_channel.id)
        state = voice.move_member(moderator.id, member.id, voice_channel2.id)

        assert state.channel_id == voice_channel2.id

    def test_move_member_without_permission(self, server_with_voice):
        """Test moving member without permission fails."""
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
            owner.id, server.id, "voice-perm2", channel_type=servers.ChannelType.VOICE
        )

        voice.join_channel(member2.id, voice_channel.id)

        with pytest.raises(voice.PermissionDeniedError) as exc_info:
            voice.move_member(member1.id, member2.id, voice_channel2.id)

        assert exc_info.value.permission == "voice.move_members"

    def test_disconnect_member_with_permission(self, server_with_moderator):
        """Test disconnecting member with permission succeeds."""
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

    def test_disconnect_member_without_permission(self, server_with_voice):
        """Test disconnecting member without permission fails."""
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

        with pytest.raises(voice.PermissionDeniedError) as exc_info:
            voice.disconnect_member(member1.id, member2.id, server.id)

        assert exc_info.value.permission == "voice.move_members"


class TestOwnerPermissions:
    """Tests for owner having all permissions."""

    def test_owner_can_mute(self, server_with_voice):
        """Test owner can server mute."""
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
        state = voice.server_mute(owner.id, member1.id, server.id)

        assert state.server_mute is True

    def test_owner_can_deaf(self, server_with_voice):
        """Test owner can server deaf."""
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
        state = voice.server_deaf(owner.id, member1.id, server.id)

        assert state.server_deaf is True

    def test_owner_can_move(self, server_with_voice):
        """Test owner can move members."""
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
            owner.id, server.id, "voice-owner", channel_type=servers.ChannelType.VOICE
        )

        voice.join_channel(member1.id, voice_channel.id)
        state = voice.move_member(owner.id, member1.id, voice_channel2.id)

        assert state.channel_id == voice_channel2.id

    def test_owner_can_disconnect(self, server_with_voice):
        """Test owner can disconnect members."""
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
        result = voice.disconnect_member(owner.id, member1.id, server.id)

        assert result is True
