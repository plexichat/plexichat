"""Regression tests for voice moderation authorization boundaries.

Owning a ``voice.*_members`` permission is not a license to moderate the
server owner or a member ranked at or above the moderator, and a moderator's
authority does not extend to voice sessions in other servers.
"""

import pytest


@pytest.mark.voice
class TestVoiceModerationHierarchy:
    def test_moderator_cannot_mute_owner(self, server_with_moderator):
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

        voice.join_channel(owner.id, voice_channel.id)

        with pytest.raises(voice.PermissionDeniedError):
            voice.server_mute(moderator.id, owner.id, server.id)

        state = voice.get_voice_state(owner.id)
        assert state.server_mute is False

    def test_moderator_cannot_deafen_owner(self, server_with_moderator):
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

        voice.join_channel(owner.id, voice_channel.id)

        with pytest.raises(voice.PermissionDeniedError):
            voice.server_deaf(moderator.id, owner.id, server.id)

        state = voice.get_voice_state(owner.id)
        assert state.server_deaf is False

    def test_moderator_cannot_move_owner(self, server_with_moderator):
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

        voice.join_channel(owner.id, voice_channel.id)
        destination = servers.create_channel(
            owner.id, server.id, "voice-dest", channel_type=servers.ChannelType.VOICE
        )

        with pytest.raises(voice.PermissionDeniedError):
            voice.move_member(moderator.id, owner.id, destination.id)

        assert voice.get_voice_state(owner.id).channel_id == voice_channel.id

    def test_moderator_cannot_disconnect_owner(self, server_with_moderator):
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

        voice.join_channel(owner.id, voice_channel.id)

        with pytest.raises(voice.PermissionDeniedError):
            voice.disconnect_member(moderator.id, owner.id, server.id)

        assert voice.get_voice_state(owner.id) is not None

    def test_moderator_cannot_moderate_higher_role(self, server_with_moderator):
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

        # Owner-created role sits above the moderator role in the hierarchy.
        senior_role = servers.create_role(
            owner.id,
            server.id,
            "Senior Moderator",
            permissions={"voice.mute_members": True},
        )
        servers.assign_role(owner.id, server.id, member.id, senior_role.id)

        voice.join_channel(member.id, voice_channel.id)

        with pytest.raises(voice.PermissionDeniedError):
            voice.server_mute(moderator.id, member.id, server.id)

        assert voice.get_voice_state(member.id).server_mute is False

    def test_moderator_can_still_moderate_lower_role(self, server_with_moderator):
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

    def test_owner_can_moderate_any_member(self, server_with_moderator):
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

        voice.join_channel(moderator.id, voice_channel.id)

        state = voice.server_mute(owner.id, moderator.id, server.id)
        assert state.server_mute is True

    def test_move_member_cannot_cross_servers(self, server_with_moderator):
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

        other_server = servers.create_server(member.id, "Other Server")
        other_channel = servers.create_channel(
            member.id,
            other_server.id,
            "other-voice",
            channel_type=servers.ChannelType.VOICE,
        )
        voice.join_channel(member.id, other_channel.id)

        with pytest.raises(voice.ChannelAccessDeniedError):
            voice.move_member(moderator.id, member.id, voice_channel.id)

        # The cross-server move must leave the target's voice state untouched.
        assert voice.get_voice_state(member.id).channel_id == other_channel.id
