"""Regression tests for stage member-target authorization boundaries.

Promoting a member to speaker (``invite_to_speak``) and demoting a speaker to
the audience (``move_to_audience``) both change another member's stage role.
Holding ``voice.mute_members`` is not sufficient on its own: the same role
hierarchy that guards server mute/deafen/move/disconnect must apply, so a
moderator cannot act on the server owner or on a peer ranked at or above them.
Self-demotion (a speaker stepping down voluntarily) remains self-service.
"""

import pytest


@pytest.mark.voice
class TestStageModerationHierarchy:
    def test_moderator_cannot_move_owner_to_audience(self, server_with_moderator):
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
        assert voice.get_voice_state(owner.id).suppress is False

        with pytest.raises(voice.PermissionDeniedError):
            voice.move_to_audience(moderator.id, owner.id, stage_channel.id)

        # The owner must still be a speaker after the denied attempt.
        assert voice.get_voice_state(owner.id).suppress is False

    def test_moderator_cannot_invite_owner_to_speak(self, server_with_moderator):
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
        # The owner steps down voluntarily so the invite path is taken.
        voice.move_to_audience(owner.id, owner.id, stage_channel.id)
        assert voice.get_voice_state(owner.id).suppress is True

        with pytest.raises(voice.PermissionDeniedError):
            voice.invite_to_speak(moderator.id, owner.id, stage_channel.id)

        assert voice.get_voice_state(owner.id).suppress is True

    def test_moderator_cannot_manage_higher_role_member(self, server_with_moderator):
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

        voice.join_channel(owner.id, stage_channel.id)
        voice.join_channel(member.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Stage")

        # Lower-ranked moderator cannot promote the senior member to speaker.
        with pytest.raises(voice.PermissionDeniedError):
            voice.invite_to_speak(moderator.id, member.id, stage_channel.id)
        assert voice.get_voice_state(member.id).suppress is True

        # Promote via the owner, then confirm the moderator cannot demote them.
        voice.invite_to_speak(owner.id, member.id, stage_channel.id)
        assert voice.get_voice_state(member.id).suppress is False

        with pytest.raises(voice.PermissionDeniedError):
            voice.move_to_audience(moderator.id, member.id, stage_channel.id)
        assert voice.get_voice_state(member.id).suppress is False

    def test_moderator_can_still_manage_lower_role_member(self, server_with_moderator):
        """The hierarchy gate must not break legitimate moderation flows."""
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
        voice.join_channel(member.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Stage")

        state = voice.invite_to_speak(moderator.id, member.id, stage_channel.id)
        assert state.suppress is False

        state = voice.move_to_audience(moderator.id, member.id, stage_channel.id)
        assert state.suppress is True

    def test_owner_can_manage_any_stage_speaker(self, server_with_moderator):
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
        voice.join_channel(moderator.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Stage")

        state = voice.invite_to_speak(owner.id, moderator.id, stage_channel.id)
        assert state.suppress is False

        state = voice.move_to_audience(owner.id, moderator.id, stage_channel.id)
        assert state.suppress is True

    def test_speaker_can_demote_self_without_permission(self, server_with_moderator):
        """Self-service stage exit stays allowed after the hierarchy gate."""
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
        voice.join_channel(member.id, stage_channel.id)
        voice.start_stage(owner.id, stage_channel.id, "Test Stage")
        voice.invite_to_speak(owner.id, member.id, stage_channel.id)

        state = voice.move_to_audience(member.id, member.id, stage_channel.id)
        assert state.suppress is True
