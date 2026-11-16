"""Regression tests for keyset (cursor) pagination on the message list path.

BEFORE: deep pages were expressed as OFFSETs that walk and discard every
skipped row, so a large channel got slower the deeper a client paged.

AFTER: pages are addressed by the last message id via an indexed ``id < ?``
range predicate with a limit+1 lookahead. These tests pin both the SQL shape
(no OFFSET) and end-to-end page correctness across many pages.
"""

import pytest

from src.core.messaging.pagination import MessageCursor
from src.core.messaging.repositories.message import MessageRepository


class _RecordingDB:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def fetch_all(self, query, params):
        self.queries.append((query, params))
        return self.rows


@pytest.mark.messaging
def test_repository_page_uses_keyset_seek_and_lookahead():
    db = _RecordingDB([])
    repo = MessageRepository(db)

    page = repo.get_by_conversation_page(42, limit=5)

    sql, params = db.queries[0]
    assert "OFFSET" not in sql.upper()
    assert "id <" not in sql
    assert "id >" not in sql
    assert "ORDER BY id DESC" in sql
    assert "LIMIT ?" in sql
    # One extra row is fetched only to compute has_more/next_cursor.
    assert params[-1] == 6
    assert page.items == []
    assert page.has_more is False
    assert page.next_cursor is None


@pytest.mark.messaging
def test_repository_followup_page_advances_the_id_cursor():
    db = _RecordingDB(
        [{"id": 400}, {"id": 390}, {"id": 380}, {"id": 370}, {"id": 360}, {"id": 350}]
    )
    repo = MessageRepository(db)
    cursor = MessageCursor.encode("before", 500)

    page = repo.get_by_conversation_page(42, limit=5, cursor=cursor)

    sql, params = db.queries[0]
    assert "OFFSET" not in sql.upper()
    assert "AND id < ?" in sql
    assert params[-2] == 500
    assert params[-1] == 6
    assert len(page.items) == 5
    assert page.has_more is True
    direction, anchor = MessageCursor.decode(page.next_cursor)
    assert direction == "before"
    assert anchor == 360


@pytest.mark.messaging
def test_cursor_roundtrip_and_malformed_tokens_are_safe():
    token = MessageCursor.encode("after", 123456789)
    assert MessageCursor.decode(token) == ("after", 123456789)
    assert MessageCursor.decode(None) == (None, None)
    assert MessageCursor.decode("not base64 !!") == (None, None)
    # Oversized tokens cannot force a huge range predicate.
    assert MessageCursor.decode("A" * 200) == (None, None)


@pytest.mark.messaging
def test_paging_a_large_conversation_visits_every_message_once(
    messaging_manager, two_users
):
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    for i in range(23):
        messaging_manager.send_message(user1.id, dm.id, f"Message {i}")

    seen = []
    cursor = None
    pages = 0
    while True:
        page = messaging_manager.get_messages_page(
            user1.id, dm.id, limit=7, cursor=cursor
        )
        pages += 1
        assert len(page.items) <= 7
        seen.extend(msg.id for msg in page.items)
        if not page.has_more:
            assert page.next_cursor is None
            break
        assert page.next_cursor is not None
        cursor = page.next_cursor
        assert pages < 10  # guard against a non-advancing cursor loop

    assert pages == 4
    assert len(seen) == 23
    assert len(set(seen)) == 23
    assert seen == sorted(seen, reverse=True)
