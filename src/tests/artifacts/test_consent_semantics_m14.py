"""Regression tests for M14: unanimous live-member consent semantics.

Recording (``RecordingManager._call_consent_allows``) requires EVERY live
``voice_states`` member to have consented. Transcription
(``transcription.worker._consent_allows``) previously proceeded with >=1
consent, so a correctly-blocked recording could later be transcribed despite
refusals (GDPR inconsistency). Both paths must now agree on unanimous
consent; these tests pin that agreement.

Each test here FAILS on the pre-fix transcription semantics (>=1 consent
allows a mixed roster) and PASSES on the unanimous rule.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.core.artifacts.recording import RecordingManager
from src.core.artifacts.transcription import worker as transcription_worker
from src.core.artifacts.transcription.worker import _consent_allows


def _recorder(db: Mock) -> RecordingManager:
    recorder = RecordingManager.__new__(RecordingManager)
    recorder._db = db
    return recorder


def _recording_allows(
    live_ids, consented, channel_id: int = 7, call_id: int = 5
) -> bool:
    """Run RecordingManager._call_consent_allows with mocked repository."""
    call = SimpleNamespace(channel_id=channel_id, consented_participants=consented)
    with (
        patch(
            "src.core.artifacts.repository.get_voice_call_uncached",
            return_value=call,
        ),
        patch(
            "src.core.artifacts.repository.get_channel_voice_user_ids",
            return_value=list(live_ids),
        ),
    ):
        return bool(_recorder(Mock())._call_consent_allows(call_id, 1))


def test_recording_requires_unanimous_live_consent():
    # Mixed roster: one live member refused -> blocked.
    assert _recording_allows([1, 2], [1]) is False
    # Full roster consented -> allowed.
    assert _recording_allows([1, 2], [1, 2]) is True
    # Single-party unanimous case still allowed.
    assert _recording_allows([1], [1]) is True
    # Nobody consented -> blocked.
    assert _recording_allows([1, 2], []) is False


def test_recording_fails_closed_without_verifiable_roster():
    # Empty live roster (lookup failure / nobody live) must fail closed,
    # never record on initiator consent alone.
    assert _recording_allows([], [1]) is False
    # Missing call row fails closed.
    with patch(
        "src.core.artifacts.repository.get_voice_call_uncached",
        return_value=None,
    ):
        assert bool(_recorder(Mock())._call_consent_allows(5, 1)) is False
    # Call without a channel cannot resolve the live roster: fail closed.
    assert _recording_allows([1], [1], channel_id=None) is False


def test_transcription_refuses_mixed_consent_roster():
    """Pre-fix code allowed transcription with only >=1 consent; now blocked."""
    call = SimpleNamespace(consented_participants=[1], participant_count=2)
    assert _consent_allows(call) is False
    assert _consent_allows(call, [1, 2]) is False
    # Unanimous consent still transcribes (incl. legitimate single-party).
    assert (
        _consent_allows(
            SimpleNamespace(consented_participants=[1, 2], participant_count=2)
        )
        is True
    )
    assert (
        _consent_allows(
            SimpleNamespace(consented_participants=[1], participant_count=1)
        )
        is True
    )
    # Roster of exactly the one consented member is unanimous -> allowed.
    assert _consent_allows(call, [1]) is True
    full = SimpleNamespace(consented_participants=[1, 2], participant_count=2)
    assert _consent_allows(full, [1, 2]) is True


def test_recording_and_transcription_agree_on_mixed_consent():
    """M14 core: both paths must give the same verdict for one roster."""
    # Mixed consent: recording blocks AND transcription refuses.
    assert _recording_allows([1, 2], [1]) is False
    mixed = SimpleNamespace(consented_participants=[1], participant_count=2)
    assert _consent_allows(mixed) is False
    assert _consent_allows(mixed, [1, 2]) is False
    # Unanimous consent: both allow.
    assert _recording_allows([1, 2], [1, 2]) is True
    full = SimpleNamespace(consented_participants=[1, 2], participant_count=2)
    assert _consent_allows(full) is True
    assert _consent_allows(full, [1, 2]) is True


def test_transcription_explicit_opt_out_still_documented(monkeypatch):
    """Explicit consent_required=False opts out (mirrors recording gate)."""
    monkeypatch.setattr(
        transcription_worker,
        "_transcription_config",
        lambda: {"consent_required": False},
    )
    call = SimpleNamespace(consented_participants=[], participant_count=2)
    assert _consent_allows(call) is True
