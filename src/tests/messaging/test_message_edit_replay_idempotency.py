"""SECURITY: a replayed/concurrent edit must not double-apply its side effects.

``edit_message`` decides "is this a no-op?" from a row it read earlier, then
writes in a separate statement (read-then-write / TOCTOU). Two applications of
the *same* edit racing each other both see the old content index, both record
an edit-history row and both drive a MESSAGE_UPDATE broadcast. The optional
``expected_edited_at`` token is checked against that same stale read, so it
did not close the race either.

The fix makes the write a compare-and-swap on the message revision; only the
winner records history, and the loser fails closed with a 409 conflict instead
of silently appending a duplicate history row. A plain sequential retry of an
identical edit remains the fast, side-effect-free no-op it always was.

Run with: pytest src/tests/messaging/test_message_edit_replay_idempotency.py -q -n 0
"""

import pytest

from src.core.messaging.exceptions import MessageConflictError

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]


def _history_count(db, message_id):
    return db.fetch_one(
        "SELECT COUNT(*) AS c FROM msg_edit_history WHERE message_id = ?",
        (message_id,),
    )["c"]


class _StaleReadRepo:
    """Freeze ``get_by_id`` to the first (pre-edit) snapshot.

    Models two edit calls that both read state before either commits; all
    other operations delegate to the real repository so writes land normally.
    """

    def __init__(self, real):
        self._real = real
        self._frozen = {}

    def __getattr__(self, name):
        return getattr(self._real, name)

    def get_by_id(self, message_id):
        if message_id not in self._frozen:
            self._frozen[message_id] = self._real.get_by_id(message_id)
        return self._frozen[message_id]

    def row_to_model(self, row, *args, **kwargs):
        return self._real.row_to_model(row, *args, **kwargs)


def test_sequential_identical_edit_is_a_noop(dm_with_message):
    """Control: a plain replay after commit leaves history untouched."""
    user1, _user2, _dm, msg, _polls, messaging = dm_with_message
    db = messaging.db

    messaging.edit_message(user1.id, msg.id, "edited once")
    assert _history_count(db, msg.id) == 1

    # Same content again -> deterministic blind-index no-op, no new history.
    messaging.edit_message(user1.id, msg.id, "edited once")
    assert _history_count(db, msg.id) == 1


def test_concurrent_same_edit_records_one_history_row(dm_with_message, monkeypatch):
    """Two racing applications of one edit commit only one history row.

    The frozen pre-edit read models the interleaving where both calls pass the
    blind-index no-op check before either write lands. The CAS on the message
    revision must let only one write advance the message: the stale loser fails
    closed with a 409 conflict instead of appending a second history row or
    returning success that would drive a second MESSAGE_UPDATE broadcast.
    """
    user1, _user2, _dm, msg, _polls, messaging = dm_with_message
    db = messaging.db

    # Assert the invariant as a delta from the baseline. A fixed absolute count
    # would be a scheduling/history-recording artifact; what matters is that the
    # race produces exactly ONE new history row for ONE logical edit.
    baseline = _history_count(db, msg.id)

    stale = _StaleReadRepo(messaging._message_svc._repo)
    monkeypatch.setattr(messaging._message_svc, "_repo", stale)

    # Winner: the first application advances the revision and records history.
    messaging.edit_message(user1.id, msg.id, "raced edit")

    # Loser: same stale pre-edit snapshot, so its CAS must fail closed. No
    # second success means no second broadcast from the route layer either.
    with pytest.raises(MessageConflictError):
        messaging.edit_message(user1.id, msg.id, "raced edit")

    # Invariant: exactly one new history row, independent of which application
    # won the race or how the interleaving was scheduled.
    assert _history_count(db, msg.id) - baseline == 1


def test_genuinely_new_edit_still_records_history(dm_with_message):
    """Control: the CAS guard must not suppress a real content change."""
    user1, _user2, _dm, msg, _polls, messaging = dm_with_message
    db = messaging.db

    messaging.edit_message(user1.id, msg.id, "first version")
    messaging.edit_message(user1.id, msg.id, "second version")

    assert _history_count(db, msg.id) == 2
