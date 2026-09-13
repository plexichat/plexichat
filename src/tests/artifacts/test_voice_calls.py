"""Regression tests for voice-call consent and recording lifecycle."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.recording import RecordingManager
from src.core.artifacts.voice_calls import VoiceCallManager
from src.core.voice.manager.calls import CallLifecycleMixin


def _seed_voice_state(db, user_id, channel_id, server_id=22):
    db.execute(
        "INSERT INTO voice_states "
        "(user_id, channel_id, server_id, joined_at, last_activity) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, channel_id, server_id, 1, 1),
    )


def _voice_manager(db, recording_manager):
    manager = VoiceCallManager(
        db,
        artifact_manager=ArtifactManager(db, {"enabled": True}),
        config={"voice": {"allow_recording": True}},
    )
    manager.set_recording_manager(recording_manager)
    return manager


def test_consent_gates_recording_and_restarts_after_new_participant(db, tmp_path):
    """A new participant stops recording until that participant consents."""
    recording = Mock()
    manager = _voice_manager(db, recording)

    _seed_voice_state(db, 33, 11)
    call = manager.start_call(channel_id=11, server_id=22, initiator_id=33)
    recording.start_call_recording.assert_called_once_with(
        call_id=call.id,
        channel_id=11,
        artifact_id=call.artifact_id,
        initiator_id=33,
    )

    # The initiator consents, satisfying the one-person call.
    manager.add_consent(call.id, 33)
    assert recording.start_call_recording.call_args.kwargs["force_restart"] is True

    # A second participant invalidates the consent threshold and stops the
    # active segment; no schema migration or second participant table is needed.
    _seed_voice_state(db, 44, 11)
    manager.add_participant(call.id, 44)
    manager.add_participant(call.id, 44)  # duplicate membership callback
    recording.stop_call_recording.assert_called_once_with(call.id)
    refreshed = manager.get_active_by_channel(11)
    assert refreshed is not None
    assert refreshed.participant_count == 2

    # Once the second participant consents, a new segment is requested and the
    # old artifact references remain accumulated by RecordingManager.
    manager.add_consent(call.id, 44)
    assert recording.start_call_recording.call_count == 3
    assert recording.start_call_recording.call_args.kwargs["force_restart"] is True


def test_rejoin_after_departure_is_treated_as_new_membership(db):
    """The real leave hook removes identity before a later rejoin."""
    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 33, 16)
    call = manager.start_call(channel_id=16, server_id=22, initiator_id=33)
    manager.add_consent(call.id, 33)

    _seed_voice_state(db, 44, 16)
    manager.add_participant(call.id, 44)
    db.execute("DELETE FROM voice_states WHERE user_id = ?", (44,))

    lifecycle = CallLifecycleMixin.__new__(CallLifecycleMixin)
    lifecycle._voice_call_manager = manager
    lifecycle.get_channel_members = lambda channel_id: manager._current_participant_ids(
        channel_id
    )
    lifecycle._get_channel_user_count = lambda channel_id: len(
        manager._current_participant_ids(channel_id)
    )
    lifecycle._on_member_left(44, 16)

    _seed_voice_state(db, 44, 16)
    manager.add_participant(call.id, 44)

    assert recording.stop_call_recording.call_count == 2


def test_recording_manager_requires_all_current_participants(db, tmp_path):
    """The recording manager compares consent IDs with participant_count."""
    call_manager = _voice_manager(db, Mock())
    _seed_voice_state(db, 33, 12)
    call = call_manager.start_call(channel_id=12, server_id=22, initiator_id=33)

    sfu = Mock()
    recorder = RecordingManager(
        db,
        media_module=Mock(),
        artifact_manager=None,
        sfu_adapter=sfu,
        recording_config={"output_dir": str(tmp_path)},
    )

    assert recorder._call_consent_allows(call.id, 33) is False
    call_manager.add_consent(call.id, 33)
    assert recorder._call_consent_allows(call.id, 33) is True
    _seed_voice_state(db, 44, 12)
    call_manager.add_participant(call.id, 44)
    assert recorder._call_consent_allows(call.id, 33) is False
    call_manager.add_consent(call.id, 44)
    assert recorder._call_consent_allows(call.id, 33) is True


def test_consent_and_participant_require_live_membership(db):
    """Consent and participant updates reject users absent from voice_states."""
    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 33, 15)
    call = manager.start_call(channel_id=15, server_id=22, initiator_id=33)

    with pytest.raises(ValueError, match="not currently in voice channel"):
        manager.add_consent(call.id, 99)
    with pytest.raises(ValueError, match="not currently in voice channel"):
        manager.add_participant(call.id, 99)


def test_revoking_consent_stops_a_live_recording(db):
    """A participant taking consent back stops the segment immediately.

    The client's dialog can answer "no"; before ``remove_consent`` there was
    no server path for that, so a recording kept running over a participant
    who had withdrawn consent -- the one outcome the unanimous-consent gate
    exists to prevent.
    """
    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 33, 41)
    call = manager.start_call(channel_id=41, server_id=22, initiator_id=33)
    manager.add_consent(call.id, 33)
    recording.stop_call_recording.reset_mock()

    manager.remove_consent(call.id, 33)

    recording.stop_call_recording.assert_called_once_with(call.id)
    refreshed = manager.get_active_by_channel(41)
    assert refreshed is not None
    assert 33 not in {int(v) for v in refreshed.consented_participants}


def test_revoking_consent_requires_live_membership(db):
    """Only a live participant may withdraw, mirroring the append's check."""
    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 33, 42)
    call = manager.start_call(channel_id=42, server_id=22, initiator_id=33)

    with pytest.raises(ValueError, match="not currently in voice channel"):
        manager.remove_consent(call.id, 99)


def test_duplicate_revoke_does_not_stop_recording_again(db):
    """A second revoke leaves an already-incomplete set alone."""
    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 33, 43)
    call = manager.start_call(channel_id=43, server_id=22, initiator_id=33)
    manager.add_consent(call.id, 33)
    manager.remove_consent(call.id, 33)
    recording.stop_call_recording.reset_mock()

    manager.remove_consent(call.id, 33)

    recording.stop_call_recording.assert_not_called()


def test_consent_can_be_regranted_after_a_revoke(db):
    """Withdrawing is not a permanent ban: a new grant records again."""
    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 33, 44)
    call = manager.start_call(channel_id=44, server_id=22, initiator_id=33)
    manager.add_consent(call.id, 33)
    manager.remove_consent(call.id, 33)
    recording.start_call_recording.reset_mock()

    manager.add_consent(call.id, 33)

    assert recording.start_call_recording.call_count == 1
    refreshed = manager.get_active_by_channel(44)
    assert refreshed is not None
    assert 33 in {int(v) for v in refreshed.consented_participants}


def test_distinct_participant_roster_persisted_and_churn_proof(db, monkeypatch):
    """The artifact payload keeps a DISTINCT participant ID roster.

    Transcription consent must require roster ⊆ consented so join/leave churn
    (which keeps the max-concurrent COUNT stale) cannot fake unanimity.
    """
    from types import SimpleNamespace

    from src.core.artifacts import voice_calls as voice_calls_module
    from src.core.artifacts.capabilities import CapabilityState
    from src.core.artifacts.transcription.worker import _consent_allows

    payloads = {}

    class FakeArtifact:
        def __init__(self, aid, payload):
            self.id = aid
            self.payload = payload

    class FakeArtifactManager:
        def __init__(self):
            self._next = 500

        def create(self, **kwargs):
            aid = self._next + 1
            self._next = aid
            payloads[aid] = dict(kwargs["payload"])
            return FakeArtifact(aid, payloads[aid])

        def get(self, aid):
            payload = payloads.get(aid)
            return FakeArtifact(aid, payload) if payload is not None else None

        def update(self, aid, **kwargs):
            if "payload" in kwargs:
                payloads[aid] = dict(kwargs["payload"])
            return FakeArtifact(aid, payloads[aid])

    monkeypatch.setattr(
        voice_calls_module,
        "get_capability",
        lambda *a, **k: SimpleNamespace(state=CapabilityState.AVAILABLE),
    )
    manager = VoiceCallManager(
        db,
        artifact_manager=FakeArtifactManager(),
        config={"voice": {"allow_recording": True}},
    )

    for uid in (11, 22, 33):
        _seed_voice_state(db, uid, 31)
    call = manager.start_call(channel_id=31, server_id=22, initiator_id=11)

    for uid in (11, 22, 33):
        manager.add_consent(call.id, uid)
    # 22 and 33 consented but then left; 44 joins and never consents.
    for uid in (22, 33):
        db.execute("DELETE FROM voice_states WHERE user_id = ?", (uid,))
        manager.remove_participant(call.id, uid)
    _seed_voice_state(db, 44, 31)
    manager.add_participant(call.id, 44)

    refreshed = manager.get_active_by_channel(31)
    assert refreshed is not None
    roster = sorted(payloads[refreshed.artifact_id]["participant_ids"])
    assert roster == [11, 22, 33, 44]

    # The legacy count-based check is DEFEATED here: three consents cover the
    # stale max-concurrent count of three.
    assert len(set(refreshed.consented_participants)) >= refreshed.participant_count
    # The persisted roster correctly blocks transcription.
    assert _consent_allows(refreshed, roster) is False


def test_duplicate_consent_does_not_restart_recording(db):
    """Repeated consent from the same current participant is idempotent."""
    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 33, 13)
    call = manager.start_call(channel_id=13, server_id=22, initiator_id=33)

    manager.add_consent(call.id, 33)
    manager.add_consent(call.id, 33)

    assert recording.start_call_recording.call_count == 2
    assert recording.start_call_recording.call_args.kwargs["force_restart"] is True


@pytest.mark.asyncio
async def test_failed_stop_keeps_queued_restart_until_retry(db, tmp_path):
    """A failed SFU stop cannot overlap a queued consent restart."""
    sfu = Mock()
    sfu.stop_recording = AsyncMock(side_effect=[RuntimeError("stop failed"), None])
    sfu.start_recording = AsyncMock(
        return_value={"recording_id": "next", "file_count": 0}
    )
    recorder = RecordingManager(
        db,
        media_module=Mock(),
        artifact_manager=None,
        sfu_adapter=sfu,
        recording_config={"output_dir": str(tmp_path), "consent_required": False},
    )
    recorder._active[7] = {
        "room_id": "voice_14",
        "artifact_id": None,
        "recording_id": "old",
        "initiator_id": 33,
        "channel_id": 14,
    }
    restart = {
        "call_id": 7,
        "channel_id": 14,
        "artifact_id": None,
        "initiator_id": 33,
    }
    recorder._pending_restarts[7] = restart

    await recorder._do_stop_recording(7)
    assert 7 in recorder._active
    assert 7 in recorder._pending_restarts
    assert sfu.start_recording.await_count == 0

    await recorder._do_stop_recording(7)
    # The successful retry consumes the queued restart and starts exactly one
    # replacement segment; it must not leave the call without recording.
    assert recorder._active[7]["recording_id"] == "next"
    assert 7 not in recorder._pending_restarts
    assert sfu.start_recording.await_count == 1


def test_concurrent_starts_spawn_single_sfu_session(db, tmp_path):
    """Racing start calls from multiple threads must yield one SFU session."""
    import asyncio as _asyncio
    import threading

    call_manager = _voice_manager(db, Mock())
    _seed_voice_state(db, 33, 17)
    call = call_manager.start_call(channel_id=17, server_id=22, initiator_id=33)
    call_manager.add_consent(call.id, 33)

    started_evt = threading.Event()
    release_evt = threading.Event()

    async def slow_start(room_id, output_dir):
        started_evt.set()
        while not release_evt.is_set():
            await _asyncio.sleep(0.01)
        return {"recording_id": "rec-1", "file_count": 0}

    sfu = Mock()
    sfu.start_recording = AsyncMock(side_effect=slow_start)
    recorder = RecordingManager(
        db,
        media_module=Mock(),
        artifact_manager=None,
        sfu_adapter=sfu,
        recording_config={"output_dir": str(tmp_path)},
    )

    threads = [
        threading.Thread(
            target=recorder.start_call_recording,
            args=(call.id, 17, call.artifact_id, 33),
        )
        for _ in range(8)
    ]
    for t in threads:
        t.start()

    if not started_evt.wait(timeout=5):
        release_evt.set()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    release_evt.set()

    # Wait for the start coroutine to finish registering state.
    for _ in range(100):
        if recorder._active.get(int(call.id)) or not recorder._starting:
            break
        threading.Event().wait(0.01)

    assert sfu.start_recording.await_count <= 1
    assert int(call.id) in recorder._active
