"""Regression tests for the single-write voice-state update path.

BEFORE: ``update_voice_state`` built one UPDATE per requested field, so a
client that sent mute+deaf+stream+video in one call issued five writes (each
touching ``last_activity``). AFTER: one scoped UPDATE covers every field, with
repeated columns collapsed to one assignment (deafen still forces mute over an
explicit unmute, preserving the security invariant).
"""

import pytest

from src.core.voice.manager.state import StateMixin


class _State:
    def __init__(self):
        self.self_mute = False
        self.self_deaf = False
        self.streaming = False
        self.video = False


class _DB:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))


class _FakeVoice:
    """Only the collaborators ``update_voice_state`` actually uses."""

    def __init__(self):
        self._db = _DB()
        self.invalidations = 0

    def get_voice_state(self, user_id):
        return _State()

    def _get_timestamp(self):
        return 12345

    def _invalidate_voice_state(self, user_id):
        self.invalidations += 1


def _update(fake, **fields):
    return StateMixin.update_voice_state(fake, 42, **fields)


@pytest.mark.unit
def test_multi_field_update_is_one_scoped_write():
    fake = _FakeVoice()
    _update(fake, self_mute=True, self_deaf=True, streaming=True, video=True)

    assert len(fake._db.calls) == 1
    sql, params = fake._db.calls[0]
    assert sql.startswith("UPDATE voice_states SET ")
    assert "WHERE user_id = ?" in sql
    assert sql.count("last_activity = ?") == 1
    for column in ("self_deaf", "self_mute", "streaming", "video"):
        assert f"{column} = ?" in sql
    assert params == (1, 1, 1, 1, 12345, 42)
    assert fake.invalidations == 1


@pytest.mark.unit
def test_deafen_forces_mute_over_explicit_unmute():
    # Deafen implies mute: a client sending self_deaf=True with an explicit
    # self_mute=False in one frame must not end up deafened-but-unmuted (the
    # peer broadcast would say the user cannot hear while their mic is live).
    # The single scoped UPDATE therefore carries one self_mute assignment whose
    # value is the forced 1, not the attacker-supplied 0.
    fake = _FakeVoice()
    _update(fake, self_deaf=True, self_mute=False)

    assert len(fake._db.calls) == 1
    sql, params = fake._db.calls[0]
    assert sql.count("self_mute = ?") == 1
    assert params[0] == 1  # self_deaf
    assert params[1] == 1  # self_mute forced on by deafen


@pytest.mark.unit
def test_no_fields_issues_no_write():
    fake = _FakeVoice()
    _update(fake)

    assert fake._db.calls == []
    assert fake.invalidations == 0


@pytest.mark.unit
def test_single_field_write_still_touches_activity_and_scopes_to_user():
    fake = _FakeVoice()
    _update(fake, video=False)

    assert len(fake._db.calls) == 1
    sql, params = fake._db.calls[0]
    assert sql == (
        "UPDATE voice_states SET video = ?, last_activity = ? WHERE user_id = ?"
    )
    assert params == (0, 12345, 42)


@pytest.mark.voice
def test_multi_field_update_persists_every_field(server_with_voice):
    (
        _owner,
        member1,
        _member2,
        _server,
        voice_channel,
        _stage_channel,
        _servers,
        voice,
    ) = server_with_voice

    voice.join_channel(member1.id, voice_channel.id)
    state = voice.update_voice_state(
        member1.id, self_mute=True, streaming=True, video=False
    )

    assert state.self_mute is True
    assert state.streaming is True
    assert state.video is False
