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
