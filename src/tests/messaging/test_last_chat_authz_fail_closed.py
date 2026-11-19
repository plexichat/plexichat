"""Fail-closed participation authorization for last-chat state.

Regression: ``LastChatService.save_last_chat`` previously guarded its
participation check with ``if self._participant_svc and ...``. When the
participant service was unavailable (messaging module not initialised, or a
manager implementation without ``_participant_svc``), the check was silently
skipped: any authenticated user could point their last-chat/recent-chat state
at an arbitrary conversation and message id they do not belong to, poisoning
session restoration with cross-conversation identifiers. Every other messaging
write path (forwarding, scheduled messages, send, ...) fails closed when the
checker is missing. These tests pin that same fail-closed policy.

Run with: pytest src/tests/messaging/test_last_chat_authz_fail_closed.py -q
"""

import pytest

from src.core.messaging.services.last_chat import LastChatService


class _DB:
    """Minimal DB that records writes and returns no existing row."""

    def __init__(self):
        self.executed = []

    def fetch_one(self, query, params=()):
        return None

    def fetch_all(self, query, params=()):
        return []

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


class _Participants:
    def __init__(self, allow=True):
        self.allow = allow
        self.checked = []

    def is_participant(self, conversation_id, user_id):
        self.checked.append((conversation_id, user_id))
        return self.allow


class _NoGetLastChat(LastChatService):
    """Isolate the write gate: skip the post-write read/cache path."""

    def get_last_chat(self, user_id):
        return {"conversation_id": 2}


@pytest.mark.messaging
class TestLastChatAuthzFailClosed:
    def test_missing_participant_service_denies_and_writes_nothing(self, monkeypatch):
        import src.core.messaging.services.last_chat as mod

        monkeypatch.setattr(mod, "cache_set", lambda *a, **k: None)
        monkeypatch.setattr(mod, "cache_delete", lambda *a, **k: None)

        db = _DB()
        svc = _NoGetLastChat(db, participant_svc=None)

        with pytest.raises(PermissionError, match="Cannot verify conversation"):
            svc.save_last_chat(
                user_id=1,
                conversation_id=2,
                last_message_id=3,
            )

        assert db.executed == [], "no state may be written when authz cannot be checked"

    def test_participant_service_denying_does_not_write(self, monkeypatch):
        import src.core.messaging.services.last_chat as mod

        monkeypatch.setattr(mod, "cache_set", lambda *a, **k: None)
        monkeypatch.setattr(mod, "cache_delete", lambda *a, **k: None)

        db = _DB()
        checker = _Participants(allow=False)
        svc = _NoGetLastChat(db, participant_svc=checker)

        with pytest.raises(PermissionError, match="Not a participant"):
            svc.save_last_chat(user_id=1, conversation_id=2, last_message_id=3)

        assert checker.checked == [(2, 1)]
        assert db.executed == []

    def test_participant_service_allowing_still_writes(self, monkeypatch):
        import src.core.messaging.services.last_chat as mod

        monkeypatch.setattr(mod, "cache_set", lambda *a, **k: None)
        monkeypatch.setattr(mod, "cache_delete", lambda *a, **k: None)

        db = _DB()
        checker = _Participants(allow=True)
        svc = _NoGetLastChat(db, participant_svc=checker)

        result = svc.save_last_chat(user_id=1, conversation_id=2, last_message_id=3)

        assert result == {"conversation_id": 2}
        assert checker.checked == [(2, 1)]
        assert db.executed, "a legitimate participant must still be able to save"
