"""Recording consent must actually reach the call manager.

The client's consent dialog answers with ``consented: true`` on
``VOICE_STATE_UPDATE``. The gateway never read that field, and
``VoiceCallManager.add_consent`` -- the only writer of
``consented_participants`` -- had **no caller anywhere in the server**, while
``RecordingManager`` refuses to start a call recording unless every live
channel member appears in that list. Recording was therefore unreachable by
construction (found by the recorded-but-never-delivered callback sweep).

These tests pin the delivery in both directions:

- the opcode forwards a granted consent scoped to the *authoritative* channel,
- it does not forward an absent or declined one,
- a consent failure never fails the state update, and
- the voice-manager helper reports rather than raises.
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
    monkeypatch.setattr(voice, "record_call_consent", record, raising=False)

    handler = VoiceHandler(servers_module=None)
    handler._dispatch_voice_state_update = AsyncMock()
    return handler, record


@pytest.mark.asyncio
async def test_state_update_delivers_a_granted_consent(monkeypatch) -> None:
    handler, record = _handler(monkeypatch)

    result = await handler.handle_voice_state_update(
        _connection(), {"channel_id": 55, "consented": True}
    )

    assert result == (None, None, None)
    record.assert_called_once_with(7, 55)


@pytest.mark.asyncio
async def test_state_update_accepts_the_string_form(monkeypatch) -> None:
    """The gateway payload is JSON; the client may send "true"."""
    handler, record = _handler(monkeypatch)

    await handler.handle_voice_state_update(
        _connection(), {"channel_id": 55, "consented": "true"}
    )

    record.assert_called_once_with(7, 55)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{"channel_id": 55}, {"channel_id": 55, "consented": False}]
)
async def test_state_update_ignores_absent_or_declined_consent(
    monkeypatch, payload
) -> None:
    """Declining means "no consent was granted" -- never a positive record."""
    handler, record = _handler(monkeypatch)

    result = await handler.handle_voice_state_update(_connection(), payload)

    assert result == (None, None, None)
    record.assert_not_called()


@pytest.mark.asyncio
async def test_consent_is_scoped_to_the_authoritative_channel(monkeypatch) -> None:
    """A payload channel the caller is not in cannot direct the consent.

    The persisted ``voice_states`` row is the authority (the caller is in 55);
    the frame claims 999, which is where a consent record must NOT land.
    """
    handler, record = _handler(monkeypatch, in_channel=55)

    await handler.handle_voice_state_update(
        _connection(), {"channel_id": 999, "consented": True}
    )

    record.assert_called_once_with(7, 55)


@pytest.mark.asyncio
async def test_a_consent_failure_does_not_fail_the_state_update(monkeypatch) -> None:
    handler, _record = _handler(monkeypatch)
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
