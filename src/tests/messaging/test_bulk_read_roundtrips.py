"""Regression tests for bulk ack/read-receipt write round trips.

BEFORE: a bulk ack with N channels called mark_read N times (each with its own
participant lookup, MAX(id), SELECT and executemany) and built unbounded
parameter lists. AFTER: one batched service call resolves all channels with
chunked queries and a bounded number of executemany writes.
"""

import pytest

from src.core.messaging.repositories.message_status import (
    _MAX_BULK_CHUNK,
    _iter_chunks,
    MessageStatusRepository,
)


class _RecordingDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.fetch_calls = []
        self.exec_calls = []

    def fetch_all(self, query, params):
        self.fetch_calls.append((query, params))
        return self.rows

    def execute_many(self, query, params_list, auto_commit=True):
        self.exec_calls.append(list(params_list))


@pytest.mark.messaging
def test_bulk_write_batches_are_bounded():
    assert list(_iter_chunks(list(range(_MAX_BULK_CHUNK + 1)))) == [
        list(range(_MAX_BULK_CHUNK)),
        [_MAX_BULK_CHUNK],
    ]

    db = _RecordingDB()
    repo = MessageStatusRepository(db)
    count = repo.batch_mark_delivered(
        1, list(range(1, _MAX_BULK_CHUNK * 2 + 2)), timestamp=123
    )

    assert count == _MAX_BULK_CHUNK * 2 + 1
    assert len(db.exec_calls) == 3
    assert all(len(chunk) <= _MAX_BULK_CHUNK for chunk in db.exec_calls)


@pytest.mark.messaging
def test_bulk_mark_read_uses_one_query_and_bounded_writes():
    db = _RecordingDB(
        [
            {"conversation_id": 5, "id": 100},
            {"conversation_id": 5, "id": 101},
            {"conversation_id": 6, "id": 200},
        ]
    )
    repo = MessageStatusRepository(db)

    counts = repo.batch_mark_read_bulk(
        7, [(5, 150, 50), (6, None, None)], timestamp=999
    )

    assert counts == {5: 2, 6: 1}
    assert len(db.fetch_calls) == 1
    sql = db.fetch_calls[0][0]
    assert "WITH receipt_ranges" in sql
    assert "OFFSET" not in sql.upper()
    assert len(db.exec_calls) == 1
    assert len(db.exec_calls[0]) == 3


@pytest.mark.messaging
def test_bulk_ack_updates_multiple_conversations(messaging_manager, user_pool):
    user = user_pool.get_user()
    other1 = user_pool.get_user()
    other2 = user_pool.get_user()
    dm1 = messaging_manager.create_dm(user.id, other1.id)
    dm2 = messaging_manager.create_dm(user.id, other2.id)

    ids1 = [
        messaging_manager.send_message(other1.id, dm1.id, f"a{i}").id for i in range(3)
    ]
    ids2 = [
        messaging_manager.send_message(other2.id, dm2.id, f"b{i}").id for i in range(4)
    ]

    counts = messaging_manager.mark_read_bulk(user.id, [(dm1.id, None), (dm2.id, None)])

    assert counts.get(dm1.id) == 3
    assert counts.get(dm2.id) == 4
    assert messaging_manager.get_last_read(user.id, dm1.id)[
        "last_read_message_id"
    ] == max(ids1)
    assert messaging_manager.get_last_read(user.id, dm2.id)[
        "last_read_message_id"
    ] == max(ids2)
