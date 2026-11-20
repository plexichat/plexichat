"""Regression tests for the single-write voice-state update path.

BEFORE: ``update_voice_state`` built one UPDATE per requested field, so a
client that sent mute+deaf+stream+video in one call issued five writes (each
touching ``last_activity``). AFTER: one scoped UPDATE covers every field, with
repeated columns collapsed to the value the old sequential writes ended on.
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
def test_repeated_column_keeps_the_old_final_value():
    # self_deaf=True forces mute on; an explicit self_mute=False must still win,
    # exactly as it did when the two columns were written by separate UPDATEs.
    fake = _FakeVoice()
    _update(fake, self_deaf=True, self_mute=False)

    assert len(fake._db.calls) == 1
    sql, params = fake._db.calls[0]
    assert sql.count("self_mute = ?") == 1
    assert params[0] == 1
    assert params[1] == 0


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
