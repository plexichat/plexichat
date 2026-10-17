"""Tuple-row adapters for the scheduled-message read path.

Admin database adapters may return mapping or tuple rows (see
docs/admin-hardening.md). These tests pin the tuple shape for
``create_scheduled_message`` (pending-count check), ``get_scheduled_message``,
``list_scheduled_messages``, ``cancel_scheduled_message``, and
``get_due_messages`` in ``src/core/messaging/services/scheduled.py``.

Run with: pytest src/tests/messaging/test_scheduled_row_shapes.py -q
"""

import time

import pytest


# A ``msg_scheduled`` row in schema order (see ``src/core/messaging/schema.py``).
_SCHEDULED_ROW = (
    5,  # id
    2,  # conversation_id
    1,  # author_id
    "hello",  # content
    1700000000000,  # scheduled_at
    "pending",  # status
    None,  # message_id
    1699999900000,  # created_at
    1699999900000,  # updated_at
    '{"a": 1}',  # attachments
)


class _TupleScheduledDB:
    """Bare-tuple rows, as a raw cursor returns them."""

    def __init__(self, one=None, all_rows=None, count=0):
        self._one = one
        self._all_rows = list(all_rows or [])
        self._count = count
        self.executed = []

    def fetch_one(self, query, params=()):
        if "COUNT(*)" in query:
            return (self._count,)
        return self._one

    def fetch_all(self, query, params=()):
        return list(self._all_rows)

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


def _make_service(db):
    from src.core.messaging.services.scheduled import ScheduledMessageService

    service = ScheduledMessageService(db)
    service._generate_id = lambda: 42
    return service


def _future_ms():
    return int(time.time() * 1000) + 600000


@pytest.mark.messaging
class TestScheduledMessageTupleRows:
    def test_create_reads_tuple_count_row(self):
        service = _make_service(_TupleScheduledDB())

        result = service.create_scheduled_message(1, 2, "hello", _future_ms())

        assert result == {}
        assert any("INSERT INTO msg_scheduled" in q for q, _ in service._db.executed)

    def test_create_enforces_limit_from_tuple_count_row(self):
        service = _make_service(_TupleScheduledDB(count=50))

        with pytest.raises(ValueError, match="Maximum 50 scheduled messages"):
            service.create_scheduled_message(1, 2, "hello", _future_ms())

    def test_get_maps_tuple_row(self):
        service = _make_service(_TupleScheduledDB(one=_SCHEDULED_ROW))

        data = service.get_scheduled_message(5, 1)

        assert data is not None
        assert data["id"] == 5
        assert data["conversation_id"] == 2
        assert data["author_id"] == 1
        assert data["content"] == "hello"
        assert data["status"] == "pending"
        assert data["attachments"] == {"a": 1}

    def test_get_maps_mapping_row(self):
        service = _make_service(
            _TupleScheduledDB(
                one={
                    "id": 5,
                    "conversation_id": 2,
                    "author_id": 1,
                    "content": "hello",
                    "scheduled_at": 1700000000000,
                    "status": "pending",
                    "message_id": None,
                    "created_at": 1699999900000,
                    "updated_at": 1699999900000,
                    "attachments": None,
                }
            )
        )

        data = service.get_scheduled_message(5, 1)

        assert data is not None
        assert data["id"] == 5
        assert data["content"] == "hello"

    def test_get_rejects_other_author_tuple_row(self):
        service = _make_service(_TupleScheduledDB(one=_SCHEDULED_ROW))

        assert service.get_scheduled_message(5, 999) is None

    def test_list_maps_tuple_rows(self):
        service = _make_service(_TupleScheduledDB(all_rows=[_SCHEDULED_ROW]))

        rows = service.list_scheduled_messages(1)

        assert len(rows) == 1
        assert rows[0]["id"] == 5
        assert rows[0]["content"] == "hello"
        assert rows[0]["attachments"] == {"a": 1}

    def test_cancel_reads_positional_author_and_status(self):
        # ``SELECT author_id, status`` column order.
        service = _make_service(_TupleScheduledDB(one=(1, "pending")))

        assert service.cancel_scheduled_message(5, 1) is True
        assert any("cancelled" in q for q, _ in service._db.executed)

    def test_cancel_rejects_other_author_tuple_row(self):
        service = _make_service(_TupleScheduledDB(one=(999, "pending")))

        with pytest.raises(PermissionError):
            service.cancel_scheduled_message(5, 1)

    def test_cancel_rejects_non_pending_tuple_row(self):
        service = _make_service(_TupleScheduledDB(one=(1, "dispatched")))

        with pytest.raises(ValueError, match="only cancel pending"):
            service.cancel_scheduled_message(5, 1)

    def test_get_due_maps_tuple_rows(self):
        service = _make_service(_TupleScheduledDB(all_rows=[_SCHEDULED_ROW]))

        rows = service.get_due_messages()

        assert len(rows) == 1
        assert rows[0]["id"] == 5
        assert rows[0]["status"] == "pending"
