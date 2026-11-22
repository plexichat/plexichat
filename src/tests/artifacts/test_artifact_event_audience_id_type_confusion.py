"""Artifact lifecycle event fan-out must not launder malformed participant ids.

``ArtifactManager._event_user_ids`` resolves the recipient audience for
artifact lifecycle events from ``msg_participants.user_id`` (a raw DB
projection returned verbatim by ``get_user_ids_by_conversation``) and hands it
to ``events.dispatch(user_ids=...)``. Applying ``int()`` to that projection
launders a malformed-but-coercible value (``"07"`` -> 7, ``7.9`` -> 7,
``True`` -> 1, ``" 8 "`` -> 8) into a genuine-looking recipient id that was
never a real participant, so the wrong principal receives the event. Only
values that are already genuine ``int`` may attend the audience.
"""

import src.api as api_mod
from src.core.artifacts.manager import ArtifactManager


class _Messaging:
    def __init__(self, ids):
        self._ids = ids

    def get_participant_ids(self, conversation_id):
        return list(self._ids)


def test_event_user_ids_rejects_coerced_participant_ids(monkeypatch):
    # Raw DB projection: genuine participants 9001 and 4242, plus malformed
    # coercible values that int() would launder into 7, 7, 1 and 8.
    raw = [9001, "07", 7.9, True, "  8  ", 4242]
    monkeypatch.setattr(api_mod, "get_messaging", lambda: _Messaging(raw))

    got = ArtifactManager._event_user_ids(12345)

    # Genuine integer participants are preserved.
    assert 9001 in got
    assert 4242 in got
    # Malformed-but-coercible values must NOT become event recipients.
    for wrong in (7, 1, 8):
        assert wrong not in got, f"laundered participant id {wrong} admitted"
    assert got == [9001, 4242]


def test_event_user_ids_preserves_all_genuine_ints(monkeypatch):
    # Non-vacuous: a clean, all-genuine projection must pass through intact.
    raw = [10, 20, 30, 20]
    monkeypatch.setattr(api_mod, "get_messaging", lambda: _Messaging(raw))
    assert ArtifactManager._event_user_ids(1) == [10, 20, 30, 20]


def test_event_user_ids_returns_none_without_messaging(monkeypatch):
    monkeypatch.setattr(api_mod, "get_messaging", lambda: None)
    assert ArtifactManager._event_user_ids(1) is None
