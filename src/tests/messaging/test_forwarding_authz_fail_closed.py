"""Fail-closed authorization for message forwarding.

Regression: ``ForwardingService.forward_message`` previously guarded its two
participation checks with ``if self._participant_svc and ...``. When the
participant service was unavailable (messaging module not initialised, or a
manager implementation without ``_participant_svc``), BOTH checks were silently
skipped: any authenticated user could read/decrypt an arbitrary message and
insert a forward record into a conversation they do not belong to. Every other
messaging service (pin, edit_history, send, retrieval, ...) fails closed when
the participation checker is missing, and ``get_forward_history`` in this same
module already does. These tests pin that same fail-closed policy for the
write path.

Run with: pytest src/tests/messaging/test_forwarding_authz_fail_closed.py -q
"""

import pytest

from src.core.messaging.services.forwarding import ForwardingService

# ``SELECT id, conversation_id, author_id, content, content_encrypted,
# created_at`` row shape used by ``forward_message``.
_MESSAGE_ROW = (20, 2, 5, "hello", None, 100)


class _DB:
    """Minimal DB record: returns a valid message and no prior forwards."""

    def __init__(self):
        self.executed = []

    def fetch_one(self, query, params=()):
        if "FROM msg_messages" in query:
            return _MESSAGE_ROW
        if "COUNT(*)" in query:
            return (0,)
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
    """Participant checker that can independently deny the two conversations."""

    def __init__(self, original=True, target=True):
        self.original = original
        self.target = target

    def is_participant(self, conversation_id, user_id):
        # conversation 2 is the source in _MESSAGE_ROW; anything else is target.
        if conversation_id == 2:
            return self.original
        return self.target


@pytest.mark.messaging
class TestForwardingAuthzFailClosed:
    def test_missing_participant_service_denies_and_writes_nothing(self):
        """No checker => deny before decrypting content or writing a record."""
        db = _DB()
        service = ForwardingService(db, None, None)
        # Even a working live "send" must not bypass the participation gate.
        service._safe_send_via_messaging = lambda *a, **k: {"id": 100}

        with pytest.raises(PermissionError):
            service.forward_message(5, 20, 99)

        assert db.executed == []

    def test_non_participant_of_original_is_denied(self):
        db = _DB()
        service = ForwardingService(db, participant_svc=_Participants(original=False))

        with pytest.raises(PermissionError):
            service.forward_message(5, 20, 99)

        assert db.executed == []

    def test_non_participant_of_target_is_denied(self):
        db = _DB()
        service = ForwardingService(db, participant_svc=_Participants(target=False))

        with pytest.raises(PermissionError):
            service.forward_message(5, 20, 99)

        assert db.executed == []
