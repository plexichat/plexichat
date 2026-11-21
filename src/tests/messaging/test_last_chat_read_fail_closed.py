"""Fail-closed participation checks on the last-chat READ paths.

``save_last_chat`` already denies when the participant checker is unavailable,
but ``get_last_chat`` and ``get_recent_chats`` used to skip their membership
verification entirely in that case (``if conv_id and self._participant_svc:``
/ ``if conv_id and checker:``).  A transient messaging-module failure therefore
turned a stale-session restore into an unchecked read of conversation ids and
message ids the viewer may no longer be allowed to see.  Authorization must
deny when it cannot be evaluated, on reads as well as writes.

Run with: pytest src/tests/messaging/test_last_chat_read_fail_closed.py -q
"""

import pytest

from src.core.messaging.services.last_chat import LastChatService

pytestmark = pytest.mark.messaging

_USER = 1
_CONV = 2
_MSG = 3

# ``user_last_chat`` row in schema order.
_LAST_CHAT_ROW = (1, _USER, _CONV, _MSG, 0, 1700000000000)
# ``get_recent_chats`` join row in SELECT order.
_RECENT_CHAT_ROW = (_CONV, 1700000000000, 0, 1700000000000, _MSG)


class _DB:
    """Minimal DB returning fixed rows and recording writes."""

    def __init__(self, one=None, recent=None):
        self._one = one
        self._recent = list(recent or [])
        self.executed = []

    def fetch_one(self, query, params=()):
        return self._one

    def fetch_all(self, query, params=()):
        return list(self._recent)

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


class _Participants:
    def __init__(self, allow):
        self.allow = allow
        self.checked = []

    def is_participant(self, conversation_id, user_id):
        self.checked.append((conversation_id, user_id))
        return self.allow


def _patch_cache(monkeypatch, cached=None):
    """Bypass Valkey and record cache deletions."""
    import src.core.messaging.services.last_chat as mod

    deleted = []
    monkeypatch.setattr(mod, "cache_get", lambda key: cached)
    monkeypatch.setattr(mod, "cache_set", lambda key, value, ttl=None: None)
    monkeypatch.setattr(mod, "cache_delete", lambda key: deleted.append(key))
    return deleted


def test_recent_chats_fails_closed_without_participant_service(monkeypatch):
    _patch_cache(monkeypatch)
    svc = LastChatService(_DB(recent=[_RECENT_CHAT_ROW]))

    assert svc.get_recent_chats(_USER) == []


def test_get_last_chat_db_path_fails_closed_without_participant_service(monkeypatch):
    _patch_cache(monkeypatch)
    db = _DB(one=_LAST_CHAT_ROW)
    svc = LastChatService(db)

    assert svc.get_last_chat(_USER) is None
    assert any("DELETE FROM user_last_chat" in q for q, _ in db.executed)


def test_get_last_chat_cache_path_fails_closed_without_participant_service(monkeypatch):
    cached = {
        "conversation_id": _CONV,
        "last_message_id": _MSG,
        "scroll_position": 0,
        "updated_at": 1700000000000,
    }
    deleted = _patch_cache(monkeypatch, cached=cached)
    svc = LastChatService(_DB())

    assert svc.get_last_chat(_USER) is None
    assert deleted == [f"last_chat:{_USER}"]


def test_denying_checker_still_filters_recent_chats(monkeypatch):
    _patch_cache(monkeypatch)
    checker = _Participants(allow=False)
    svc = LastChatService(_DB(recent=[_RECENT_CHAT_ROW]), checker)

    assert svc.get_recent_chats(_USER) == []
    assert checker.checked == [(_CONV, _USER)]


def test_allowing_checker_still_returns_recent_chats(monkeypatch):
    _patch_cache(monkeypatch)
    checker = _Participants(allow=True)
    svc = LastChatService(_DB(recent=[_RECENT_CHAT_ROW]), checker)

    rows = svc.get_recent_chats(_USER)

    assert len(rows) == 1
    assert int(rows[0]["conversation_id"]) == _CONV
    assert checker.checked == [(_CONV, _USER)]
