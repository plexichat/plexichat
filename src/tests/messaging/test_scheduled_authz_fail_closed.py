"""Fail-closed participation authorization for scheduled messages.

Regression: ``ScheduledMessageService.create_scheduled_message`` used to guard
its participation check with ``if self._participant_svc and ...``. The route
derives that service with ``getattr(messaging, "_participant_svc", None)``,
so when the messaging module was not initialized (or a manager implementation
lacked the attribute) the check was silently skipped. Any authenticated user
could then schedule a message into a conversation they do not belong to, which
is later delivered to that conversation. Every other messaging write path
(forwards, voice, last-chat) denies when the checker is unavailable; these
tests pin that same policy for scheduled messages.

Run with: pytest src/tests/messaging/test_scheduled_authz_fail_closed.py -q
"""

import time

import pytest

from src.core.messaging.services.scheduled import ScheduledMessageService

USER_ID = 5
FOREIGN_CONVERSATION_ID = 99


class _DB:
    """Minimal DB stub: no pending messages, no stored scheduled row."""

    def __init__(self):
        self.executed = []

    def fetch_one(self, query, params=()):
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
    def __init__(self, allowed: bool):
        self.allowed = allowed
        self.checked = []

    def is_participant(self, conversation_id, user_id):
        self.checked.append((conversation_id, user_id))
        return self.allowed


def _future_ms() -> int:
    return int(time.time() * 1000) + 120_000


@pytest.mark.messaging
class TestScheduledAuthzFailClosed:
    def test_missing_participant_service_denies_and_writes_nothing(self):
        db = _DB()
        service = ScheduledMessageService(db, None)

        with pytest.raises(PermissionError):
            service.create_scheduled_message(
                USER_ID, FOREIGN_CONVERSATION_ID, "later", _future_ms()
            )

        assert db.executed == []

    def test_checker_exception_denies_and_writes_nothing(self):
        db = _DB()
        participants = _Participants(allowed=False)
        service = ScheduledMessageService(db, participants)

        with pytest.raises(PermissionError):
            service.create_scheduled_message(
                USER_ID, FOREIGN_CONVERSATION_ID, "later", _future_ms()
            )

        assert participants.checked == [(FOREIGN_CONVERSATION_ID, USER_ID)]
        assert db.executed == []

    def test_participant_can_still_schedule(self):
        """A real participant is unaffected by the fail-closed change."""
        db = _DB()
        participants = _Participants(allowed=True)
        service = ScheduledMessageService(db, participants)

        result = service.create_scheduled_message(
            USER_ID, FOREIGN_CONVERSATION_ID, "later", _future_ms()
        )

        assert participants.checked == [(FOREIGN_CONVERSATION_ID, USER_ID)]
        inserts = [q for q, _ in db.executed if "INSERT INTO msg_scheduled" in q]
        assert len(inserts) == 1
        assert result == {}
