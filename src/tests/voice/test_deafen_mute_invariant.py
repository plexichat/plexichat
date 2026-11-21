"""Regression: a client cannot spoof a deafened-but-unmuted voice state.

``VOICE_STATE_UPDATE`` carries ``self_mute`` and ``self_deaf`` independently,
so a single frame can assert ``self_deaf=true`` together with
``self_mute=false``. The batched ``update_voice_state`` path used to let that
explicit unmute overwrite the implicit mute that deafening applies, producing
a state that is broadcast to every other channel member as "deafened" (peers
are told the user cannot hear them) while the user's own audio stays live.
Deafening must always imply muting, exactly as it does on the single-field
``set_self_deaf`` path.
"""

import pytest


@pytest.mark.voice
class TestDeafenImpliesMute:
    def test_client_cannot_unmute_while_deafening(self, server_with_voice):
        (
            owner,
            member,
            other,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        voice.join_channel(member.id, voice_channel.id)

        state = voice.update_voice_state(member.id, self_deaf=True, self_mute=False)

        assert state.self_deaf is True
        # The spoofed explicit unmute must not win over deafen's implicit mute.
        assert state.self_mute is True

    def test_explicit_unmute_still_works_when_not_deafening(self, server_with_voice):
        (
            owner,
            member,
            other,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        voice.join_channel(member.id, voice_channel.id)
        voice.update_voice_state(member.id, self_deaf=True)

        # Undeafening with an explicit unmute is a legitimate self-service
        # transition and must remain possible; the fix must not blanket-ignore
        # self_mute.
        state = voice.update_voice_state(member.id, self_deaf=False, self_mute=False)
        assert state.self_deaf is False
        assert state.self_mute is False

    def test_undeafen_keeps_explicit_mute(self, server_with_voice):
        (
            owner,
            member,
            other,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        voice.join_channel(member.id, voice_channel.id)
        voice.update_voice_state(member.id, self_deaf=True)

        # A user may step down from deafening while staying muted.
        state = voice.update_voice_state(member.id, self_deaf=False, self_mute=True)
        assert state.self_deaf is False
        assert state.self_mute is True
