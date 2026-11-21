"""Regression: bookmark listing must fail closed when the view gate is absent.

``BookmarkService.get_bookmarks`` documents that rows pointing at messages the
caller can no longer view are silently dropped so a listing can never leak
inaccessible message metadata. Two paths violated that contract:

* with no messaging module wired in, every raw bookmark row was returned
  unchecked;
* when message enrichment raised, the row was kept (only the preview was
  skipped) instead of being dropped.

Both are fail-open: a revoked participant or a transient gate error turns into
a leak of ``message_id`` / ``conversation_id`` / ``label`` / ``created_at`` for
a message the caller is not allowed to see. The dedicated ``_is_viewable_by``
helper performs a fail-closed probe and is now used on both paths.
"""

from types import SimpleNamespace

from src.core.messaging.services.bookmarks import BookmarkService

USER = 991001
BOOKMARK = {
    "id": 991010,
    "user_id": USER,
    "message_id": 991020,
    "conversation_id": 991030,
    "label": "private-note",
    "created_at": 1700000000000,
}


class _DB:
    """Minimal DB double: one bookmark row plus configurable view-gate rows."""

    def __init__(self, *, message_conversation, participant):
        self._message_conversation = message_conversation
        self._participant = participant
        self.message_probes = 0
        self.participant_probes = 0

    def fetch_all(self, query, params=None):
        return [dict(BOOKMARK)]

    def fetch_one(self, query, params=None):
        normalized = " ".join(query.split())
        if "FROM msg_messages" in normalized:
            self.message_probes += 1
            if self._message_conversation is None:
                return None
            return {"conversation_id": self._message_conversation}
        if "FROM msg_participants" in normalized:
            self.participant_probes += 1
            return (1,) if self._participant else None
        return None

    def execute(self, query, params=None):
        return None


def test_list_without_messaging_drops_unviewable_row():
    """No messaging module -> DB fallback still hides inaccessible messages."""
    db = _DB(message_conversation=BOOKMARK["conversation_id"], participant=False)
    assert BookmarkService(db).get_bookmarks(USER) == []
    assert db.message_probes == 1
    assert db.participant_probes == 1


def test_list_without_messaging_keeps_viewable_row():
    """The fallback is fail-closed, not fail-empty: real bookmarks survive."""
    db = _DB(message_conversation=BOOKMARK["conversation_id"], participant=True)
    rows = BookmarkService(db).get_bookmarks(USER)
    assert [row["message_id"] for row in rows] == [BOOKMARK["message_id"]]
    assert rows[0]["label"] == "private-note"


def test_list_drops_row_when_view_gate_raises():
    """A throwing get_message must not leave metadata in the response."""

    class _ThrowingMessaging:
        def get_message(self, user_id, message_id):
            raise RuntimeError("participant service unavailable")

    # get_message raises inside _is_viewable_by too, so the gate fails closed.
    db = _DB(message_conversation=BOOKMARK["conversation_id"], participant=False)
    svc = BookmarkService(db, _ThrowingMessaging())
    assert svc.get_bookmarks(USER) == []


def test_list_keeps_viewable_row_on_normal_path():
    """Unchanged happy path still enriches viewable messages."""

    class _Messaging:
        def get_message(self, user_id, message_id):
            return SimpleNamespace(content="secret", author_id=7, created_at=123)

    db = _DB(message_conversation=BOOKMARK["conversation_id"], participant=True)
    rows = BookmarkService(db, _Messaging()).get_bookmarks(USER)
    assert len(rows) == 1
    assert rows[0]["content_preview"] == "secret"
    assert rows[0]["author_id"] == 7
