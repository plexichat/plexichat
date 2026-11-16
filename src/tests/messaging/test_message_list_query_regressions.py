"""Query-count regression tests for the message list path.

These pin the property that every message-list read costs a *constant* number
of SQL round trips regardless of how many messages are on the page. They guard
against reintroducing per-message author/attachment/pin lookups, an OFFSET
scan, or unbounded serializers on hot channel pages.
"""

import pytest

from src.core.messaging.repositories.attachment import AttachmentRepository
from src.core.messaging.repositories.message import MessageRepository
from src.core.messaging.repositories.pin import PinRepository


class _CountingDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.fetch_all_calls = []
        self.fetch_one_calls = []

    def fetch_all(self, query, params=None):
        self.fetch_all_calls.append((query, params))
        return self.rows

    def fetch_one(self, query, params=None):
        self.fetch_one_calls.append((query, params))
        return None


def _rows(count):
    return [
        {
            "id": 1000 + i,
            "conversation_id": 1,
            "author_id": 7,
            "content": f"message {i}",
            "message_type": "text",
            "created_at": 1_700_000_000_000 + i,
            "updated_at": 1_700_000_000_000 + i,
            "deleted": 0,
            "metadata": None,
        }
        for i in range(count)
    ]


@pytest.mark.messaging
@pytest.mark.parametrize("page_size", [1, 10, 100])
def test_message_rows_cost_one_fetch_regardless_of_page_size(page_size):
    # Vary the conversation id so the @cached repository decorator cannot
    # serve a prior parametrization from its L1 tier.
    conversation_id = 9000 + page_size
    db = _CountingDB(_rows(page_size))
    repo = MessageRepository(db)

    rows = repo.get_by_conversation(conversation_id, limit=100)

    assert len(rows) == page_size
    assert len(db.fetch_all_calls) == 1
    sql = db.fetch_all_calls[0][0]
    assert "LIMIT ?" in sql
    assert "OFFSET" not in sql.upper()
    assert db.fetch_one_calls == []


@pytest.mark.messaging
@pytest.mark.parametrize("message_count", [10, 100])
def test_pin_and_attachment_enrichment_stay_one_query_each(message_count):
    ids = list(range(1, message_count + 1))
    pin_db = _CountingDB([])
    attachment_db = _CountingDB([])

    PinRepository(pin_db).get_batch_by_messages(ids)
    AttachmentRepository(attachment_db).get_batch_by_messages(ids)

    assert len(pin_db.fetch_all_calls) == 1
    assert len(attachment_db.fetch_all_calls) == 1


@pytest.mark.messaging
def test_real_db_list_query_count_is_independent_of_message_count(
    db, messaging_manager, user_pool, monkeypatch
):
    user = user_pool.get_user()
    small_dm = messaging_manager.create_dm(user.id, user_pool.get_user().id)
    large_dm = messaging_manager.create_dm(user.id, user_pool.get_user().id)
    for i in range(10):
        messaging_manager.send_message(user.id, small_dm.id, f"small {i}")
    for i in range(100):
        messaging_manager.send_message(user.id, large_dm.id, f"large {i}")

    def count_queries(conversation_id):
        counts = {"fetch_all": 0, "fetch_one": 0}
        original_all = db.fetch_all
        original_one = db.fetch_one

        def counted_all(query, params=None):
            counts["fetch_all"] += 1
            return original_all(query, params)

        def counted_one(query, params=None):
            counts["fetch_one"] += 1
            return original_one(query, params)

        monkeypatch.setattr(db, "fetch_all", counted_all)
        monkeypatch.setattr(db, "fetch_one", counted_one)
        try:
            messages = messaging_manager.get_messages(
                user.id, conversation_id, limit=100
            )
        finally:
            monkeypatch.setattr(db, "fetch_all", original_all)
            monkeypatch.setattr(db, "fetch_one", original_one)
        return len(messages), counts

    small_len, small_counts = count_queries(small_dm.id)
    large_len, large_counts = count_queries(large_dm.id)

    assert small_len == 10
    assert large_len == 100
    # Batched hydration means a 10x larger page does not add per-message
    # queries; a fixed +/-1 covers cache/participant probe differences.
    assert abs(large_counts["fetch_all"] - small_counts["fetch_all"]) <= 1
    assert abs(large_counts["fetch_one"] - small_counts["fetch_one"]) <= 1
    assert large_counts["fetch_all"] <= 12
