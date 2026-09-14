"""Recording consent must actually reach the call manager.

The client's consent dialog answers with ``consented: true`` on
``VOICE_STATE_UPDATE``. The gateway never read that field, and
``VoiceCallManager.add_consent`` -- the only writer of
``consented_participants`` -- had **no caller anywhere in the server**, while
``RecordingManager`` refuses to start a call recording unless every live
channel member appears in that list. Recording was therefore unreachable by
construction (found by the recorded-but-never-delivered callback sweep).

The same field answers "no" (``consented: false``), and that had no server
path either: a participant could never take consent back once the recording
gate had opened. A decline is now delivered as a revocation and stops any live
recording segment.

These tests pin the delivery in both directions:

- the opcode forwards a granted consent scoped to the *authoritative* channel,
- it forwards a decline as a revocation of the same channel,
- an absent value is not a decision and forwards nothing,
- a consent failure never fails the state update, and
- the voice-manager helpers report rather than raise.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.core.voice as voice
from src.api.websocket.handlers.voice import VoiceHandler
from src.core.voice.manager.calls import CallLifecycleMixin


def _connection(user_id: int = 7):
    return SimpleNamespace(is_authenticated=True, user_id=user_id)


def _handler(monkeypatch, *, in_channel=55):
    monkeypatch.setattr(
        voice,
        "update_voice_state",
        lambda **kwargs: SimpleNamespace(
            channel_id=in_channel, self_mute=False, self_deaf=False
        ),
    )
    record = MagicMock(return_value=True)
    revoke = MagicMock(return_value=True)
    monkeypatch.setattr(voice, "record_call_consent", record, raising=False)
    monkeypatch.setattr(voice, "revoke_call_consent", revoke, raising=False)

    handler = VoiceHandler(servers_module=None)
    handler._dispatch_voice_state_update = AsyncMock()
    return handler, record, revoke


@pytest.mark.asyncio
async def test_state_update_delivers_a_granted_consent(monkeypatch) -> None:
    handler, record, revoke = _handler(monkeypatch)

    result = await handler.handle_voice_state_update(
        _connection(), {"channel_id": 55, "consented": True}
    )

    assert result == (None, None, None)
    record.assert_called_once_with(7, 55)
    revoke.assert_not_called()


@pytest.mark.asyncio
async def test_state_update_accepts_the_string_form(monkeypatch) -> None:
    """The gateway payload is JSON; the client may send "true"."""
    handler, record, _revoke = _handler(monkeypatch)

    await handler.handle_voice_state_update(
        _connection(), {"channel_id": 55, "consented": "true"}
    )

    record.assert_called_once_with(7, 55)


@pytest.mark.asyncio
async def test_state_update_ignores_an_absent_consent(monkeypatch) -> None:
    """No field is not a decision: neither grant nor revoke fires."""
    handler, record, revoke = _handler(monkeypatch)

    result = await handler.handle_voice_state_update(_connection(), {"channel_id": 55})

    assert result == (None, None, None)
    record.assert_not_called()
    revoke.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("declined", [False, "false"])
async def test_state_update_delivers_a_declined_consent_as_a_revoke(
    monkeypatch, declined
) -> None:
    """A decline is a real answer: it withdraws consent, it is not ignored.

    The client's dialog answers "no" with ``consented: false``; before this
    path existed a participant could never take consent back, so a recording
    that they had once allowed kept running over them.
    """
    handler, record, revoke = _handler(monkeypatch)

    result = await handler.handle_voice_state_update(
        _connection(), {"channel_id": 55, "consented": declined}
    )

    assert result == (None, None, None)
    revoke.assert_called_once_with(7, 55)
    record.assert_not_called()


@pytest.mark.asyncio
async def test_consent_is_scoped_to_the_authoritative_channel(monkeypatch) -> None:
    """A payload channel the caller is not in cannot direct the consent.

    The persisted ``voice_states`` row is the authority (the caller is in 55);
    the frame claims 999, which is where a consent record must NOT land.
    """
    handler, record, _revoke = _handler(monkeypatch, in_channel=55)

    await handler.handle_voice_state_update(
        _connection(), {"channel_id": 999, "consented": True}
    )

    record.assert_called_once_with(7, 55)


@pytest.mark.asyncio
async def test_a_consent_failure_does_not_fail_the_state_update(monkeypatch) -> None:
    handler, _record, _revoke = _handler(monkeypatch)
    monkeypatch.setattr(
        voice,
        "record_call_consent",
        MagicMock(side_effect=RuntimeError("call manager exploded")),
        raising=False,
    )

    result = await handler.handle_voice_state_update(
        _connection(), {"channel_id": 55, "consented": True}
    )

    assert result == (None, None, None)


# === the voice-manager helper ==============================================


class _ConsentHarness(CallLifecycleMixin):
    """Bare host for the mixin: only the call-manager slot is exercised."""

    def __init__(self, manager):
        self._voice_call_manager = manager


def test_records_consent_against_the_active_call() -> None:
    manager = MagicMock()
    manager.get_active_by_channel.return_value = SimpleNamespace(id=4242)
    harness = _ConsentHarness(manager)

    assert harness.record_call_consent(7, 55) is True
    manager.get_active_by_channel.assert_called_once_with(55)
    manager.add_consent.assert_called_once_with(4242, 7)


def test_revokes_consent_against_the_active_call() -> None:
    manager = MagicMock()
    manager.get_active_by_channel.return_value = SimpleNamespace(id=4242)
    harness = _ConsentHarness(manager)

    assert harness.revoke_call_consent(7, 55) is True
    manager.get_active_by_channel.assert_called_once_with(55)
    manager.remove_consent.assert_called_once_with(4242, 7)


def test_revoke_reports_false_without_a_call_manager() -> None:
    assert _ConsentHarness(None).revoke_call_consent(7, 55) is False


def test_revoke_reports_false_when_nothing_held_the_consent() -> None:
    """Nothing revoked anywhere -- not merely "no active call in this channel".

    The withdrawal is no longer scoped to the channel the dialog named, so a
    falsey report now means the user was not live in any recorded call and no
    live segment covered them.
    """
    manager = MagicMock()
    manager.get_active_by_channel.return_value = None
    manager.revoke_consent_everywhere.return_value = []
    manager.stop_live_recordings_for_user.return_value = []
    harness = _ConsentHarness(manager)

    assert harness.revoke_call_consent(7, 55) is False
    manager.remove_consent.assert_not_called()


def test_revoke_asks_for_every_call_the_user_is_live_in() -> None:
    manager = MagicMock()
    manager.get_active_by_channel.return_value = None
    manager.revoke_consent_everywhere.return_value = [4242]
    manager.stop_live_recordings_for_user.return_value = []
    harness = _ConsentHarness(manager)

    assert harness.revoke_call_consent(7, 55) is True
    manager.revoke_consent_everywhere.assert_called_once_with(7)


def test_revoke_stops_a_live_segment_beyond_the_active_call() -> None:
    """The ANNEX AB residual: a segment outliving the call it belongs to.

    There is no active call left to withdraw from, but a recording segment
    still covers the user. A withdrawal scoped to the active call would leave
    it running over them.
    """
    manager = MagicMock()
    manager.get_active_by_channel.return_value = None
    manager.revoke_consent_everywhere.return_value = []
    manager.stop_live_recordings_for_user.return_value = [4242]
    harness = _ConsentHarness(manager)

    assert harness.revoke_call_consent(7, 55) is True
    manager.stop_live_recordings_for_user.assert_called_once_with(7)


def test_revoke_reports_false_when_the_core_refuses() -> None:
    """``remove_consent`` re-checks live membership and raises for a non-member."""
    manager = MagicMock()
    manager.get_active_by_channel.return_value = SimpleNamespace(id=4242)
    manager.remove_consent.side_effect = ValueError("not currently in voice channel")
    manager.revoke_consent_everywhere.return_value = []
    manager.stop_live_recordings_for_user.return_value = []
    harness = _ConsentHarness(manager)

    assert harness.revoke_call_consent(7, 55) is False


def test_reports_false_without_a_call_manager() -> None:
    assert _ConsentHarness(None).record_call_consent(7, 55) is False


def test_reports_false_when_no_call_is_active() -> None:
    manager = MagicMock()
    manager.get_active_by_channel.return_value = None
    harness = _ConsentHarness(manager)

    assert harness.record_call_consent(7, 55) is False
    manager.add_consent.assert_not_called()


def test_reports_false_when_the_core_refuses() -> None:
    """``add_consent`` re-checks live membership and raises for a non-member."""
    manager = MagicMock()
    manager.get_active_by_channel.return_value = SimpleNamespace(id=4242)
    manager.add_consent.side_effect = ValueError("not currently in voice channel")
    harness = _ConsentHarness(manager)

    assert harness.record_call_consent(7, 55) is False


# === the module-level wrapper ==============================================


def test_module_helper_delegates(monkeypatch) -> None:
    manager = MagicMock()
    manager.record_call_consent.return_value = True
    monkeypatch.setattr(voice, "_manager", manager)
    monkeypatch.setattr(voice, "_setup_complete", True)

    assert voice.record_call_consent(7, 55) is True
    manager.record_call_consent.assert_called_once_with(7, 55)


def test_module_helper_is_a_no_op_when_unset(monkeypatch) -> None:
    monkeypatch.setattr(voice, "_manager", None)
    monkeypatch.setattr(voice, "_setup_complete", False)

    assert voice.record_call_consent(7, 55) is False


def test_revoke_module_helper_delegates(monkeypatch) -> None:
    manager = MagicMock()
    manager.revoke_call_consent.return_value = True
    monkeypatch.setattr(voice, "_manager", manager)
    monkeypatch.setattr(voice, "_setup_complete", True)

    assert voice.revoke_call_consent(7, 55) is True
    manager.revoke_call_consent.assert_called_once_with(7, 55)


def test_revoke_keeps_going_when_the_scoped_call_refuses() -> None:
    """One call refusing must not swallow the rest of the withdrawal."""
    manager = MagicMock()
    manager.get_active_by_channel.return_value = SimpleNamespace(id=4242)
    manager.remove_consent.side_effect = ValueError("not currently in voice channel")
    manager.revoke_consent_everywhere.return_value = []
    manager.stop_live_recordings_for_user.return_value = [4343]
    harness = _ConsentHarness(manager)

    assert harness.revoke_call_consent(7, 55) is True
    manager.stop_live_recordings_for_user.assert_called_once_with(7)


def test_revoke_module_helper_is_a_no_op_when_unset(monkeypatch) -> None:
    monkeypatch.setattr(voice, "_manager", None)
    monkeypatch.setattr(voice, "_setup_complete", False)

    assert voice.revoke_call_consent(7, 55) is False
