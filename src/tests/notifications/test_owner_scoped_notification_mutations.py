"""Regression: notification lookups and mutations must be owner-scoped.

A user-supplied notification id is an untrusted identifier. The lookup must
not return another user's notification (IDOR), and the write that follows
must carry the owner predicate itself rather than relying on a prior check
(TOCTOU). Both properties are asserted here at the manager boundary.
"""

import pytest

from src.core.notifications.exceptions import NotificationNotFoundError
from src.core.notifications.manager.feed import FeedMixin

pytestmark = [pytest.mark.unit, pytest.mark.security]


OWNER = 100
ATTACKER = 200


def _row(nid, user_id):
    return {
        "id": nid,
        "user_id": user_id,
        "sender_id": 9,
        "message_id": 500,
        "conversation_id": 600,
        "server_id": None,
        "channel_id": None,
        "thread_id": None,
        "mention_type": "user",
        "content_preview": "private body",
        "content_preview_encrypted": None,
        "read": 0,
        "created_at": 1234,
    }


class _FakeDB:
    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.statements = []

    def fetch_one(self, sql, params=()):
        self.statements.append(("fetch_one", sql, tuple(params)))
        scoped = "AND user_id = ?" in sql
        nid = params[0]
        row = self.rows.get(nid)
        if row is None:
            return None
        if scoped and row["user_id"] != params[1]:
            return None
        return dict(row)

    def execute(self, sql, params=()):
        self.statements.append(("execute", sql, tuple(params)))

        class Result:
            rowcount = 0

        result = Result()
        nid, uid = params
        row = self.rows.get(nid)
        if row is None or row["user_id"] != uid:
            # The owner predicate must make a cross-user write a no-op even
            # if a caller reached this far with someone else's id.
            return result
        if sql.startswith("UPDATE") and row["read"] == 0:
            row["read"] = 1
            result.rowcount = 1
        elif sql.startswith("DELETE"):
            del self.rows[nid]
            result.rowcount = 1
        return result


class _Feed(FeedMixin):
    def __init__(self, db):
        self._db = db
        self._config = {"max_notifications_per_page": 100, "max_feed_items": 100}
        self.events = []

    def _decrement_mention_count(self, *args, **kwargs):
        pass

    def _decrement_unread_count(self, *args, **kwargs):
        pass

    def _dispatch_notification_event(self, user_id, event_type, data):
        self.events.append((user_id, event_type, data))


def _manager():
    db = _FakeDB([_row(1, OWNER), _row(2, ATTACKER)])
    return _Feed(db), db


def test_scoped_lookup_cannot_return_another_users_notification():
    feed, db = _manager()
    assert feed.get_notification(1, OWNER) is not None
    assert feed.get_notification(1, ATTACKER) is None
    assert any("AND user_id = ?" in sql for _, sql, _ in db.statements)


def test_mark_read_with_another_users_id_does_not_mutate_the_row():
    feed, db = _manager()
    with pytest.raises(NotificationNotFoundError):
        feed.mark_notification_read(ATTACKER, 1)
    assert db.rows[1]["read"] == 0
    assert feed.events == []
    # The scoped lookup rejects the cross-user id before any write is even
    # attempted; no cross-user UPDATE reaches the database.
    assert not [s for s in db.statements if s[0] == "execute"]


def test_delete_with_another_users_id_does_not_delete_the_row():
    feed, db = _manager()
    with pytest.raises(NotificationNotFoundError):
        feed.delete_notification(ATTACKER, 1)
    assert 1 in db.rows
    assert feed.events == []
    assert not [s for s in db.statements if s[0] == "execute"]


def test_owner_can_still_read_and_delete_their_own_notification():
    feed, db = _manager()
    assert feed.mark_notification_read(OWNER, 1) is True
    assert db.rows[1]["read"] == 1
    assert feed.delete_notification(OWNER, 1) is True
    assert 1 not in db.rows
    writes = [s for s in db.statements if s[0] == "execute"]
    assert len(writes) == 2
    # Both mutations carry the owner predicate in the SQL itself.
    assert all("user_id = ?" in sql for _, sql, _ in writes)
