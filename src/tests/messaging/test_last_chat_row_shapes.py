"""Tuple-row adapters for the last-chat read path.

Admin database adapters may return mapping or tuple rows (see
docs/admin-hardening.md). These tests pin the tuple shape for
``get_last_chat`` (``SELECT *`` in schema order), ``get_recent_chats``
(explicit join-column order), and ``_add_to_history`` (pending-count check)
in ``src/core/messaging/services/last_chat.py``, plus the lower clamp on
``get_recent_chats`` (SQLite treats ``LIMIT -1`` as unlimited).

Run with: pytest src/tests/messaging/test_last_chat_row_shapes.py -q
"""

import pytest


# A ``user_last_chat`` row in schema order (see
# ``src/core/chat_tracking/schema.py``).
_LAST_CHAT_ROW = (
    7,  # id
    1,  # user_id
    2,  # conversation_id
    3,  # last_message_id
    0,  # scroll_position
    1700000000000,  # updated_at
)

# A ``get_recent_chats`` join row in SELECT order
# (conversation_id, accessed_at, unread_count, last_message_at,
# last_message_id).
_RECENT_CHAT_ROW = (
    2,  # conversation_id
    1700000000000,  # accessed_at
    0,  # unread_count
    1700000000000,  # last_message_at
    9,  # last_message_id
)


class _TupleLastChatDB:
    """Bare-tuple rows, as a raw cursor returns them."""

    def __init__(self, one=None, all_rows=None, count=0):
        self._one = one
        self._all_rows = list(all_rows or [])
        self._count = count
        self.executed = []
        self.last_fetch_all_params = None

    def fetch_one(self, query, params=()):
        if "COUNT(*)" in query:
            return (self._count,)
        return self._one

    def fetch_all(self, query, params=()):
        self.last_fetch_all_params = params
        return list(self._all_rows)

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


def _make_service(db, monkeypatch):
    from src.core.messaging.services.last_chat import LastChatService
    import src.core.messaging.services.last_chat as last_chat_module

    service = LastChatService(db)
    service._generate_id = lambda: 42
    # Bypass the Valkey cache so every test exercises the database path.
    monkeypatch.setattr(last_chat_module, "cache_get", lambda key: None)
    monkeypatch.setattr(
        last_chat_module, "cache_set", lambda key, value, ttl=None: None
    )
    monkeypatch.setattr(last_chat_module, "cache_delete", lambda key: None)
    return service


@pytest.mark.messaging
class TestLastChatTupleRows:
    def test_get_maps_tuple_row(self, monkeypatch):
        service = _make_service(_TupleLastChatDB(one=_LAST_CHAT_ROW), monkeypatch)

        data = service.get_last_chat(1)

        assert data is not None
        assert data["conversation_id"] == 2
        assert data["last_message_id"] == 3
        assert data["scroll_position"] == 0
        assert data["updated_at"] == 1700000000000

    def test_get_maps_mapping_row(self, monkeypatch):
        service = _make_service(
            _TupleLastChatDB(
                one={
                    "id": 7,
                    "user_id": 1,
                    "conversation_id": 2,
                    "last_message_id": 3,
                    "scroll_position": 0,
                    "updated_at": 1700000000000,
                }
            ),
            monkeypatch,
        )

        data = service.get_last_chat(1)

        assert data is not None
        assert data["conversation_id"] == 2
        assert data["last_message_id"] == 3

    def test_get_returns_none_when_missing(self, monkeypatch):
        service = _make_service(_TupleLastChatDB(one=None), monkeypatch)

        assert service.get_last_chat(1) is None

    def test_recent_maps_tuple_rows(self, monkeypatch):
        service = _make_service(
            _TupleLastChatDB(all_rows=[_RECENT_CHAT_ROW]), monkeypatch
        )

        rows = service.get_recent_chats(1)

        assert len(rows) == 1
        assert rows[0]["conversation_id"] == 2
        assert rows[0]["accessed_at"] == 1700000000000
        assert rows[0]["unread_count"] == 0
        assert rows[0]["last_message_at"] == 1700000000000
        assert rows[0]["last_message_id"] == 9

    def test_recent_maps_mapping_rows(self, monkeypatch):
        service = _make_service(
            _TupleLastChatDB(
                all_rows=[
                    {
                        "conversation_id": 2,
                        "accessed_at": 1700000000000,
                        "unread_count": 1,
                        "last_message_at": 1700000000000,
                        "last_message_id": 9,
                    }
                ]
            ),
            monkeypatch,
        )

        rows = service.get_recent_chats(1)

        assert len(rows) == 1
        assert rows[0]["conversation_id"] == 2
        assert rows[0]["unread_count"] == 1

    def test_recent_clamps_negative_limit(self, monkeypatch):
        db = _TupleLastChatDB(all_rows=[])
        service = _make_service(db, monkeypatch)

        assert service.get_recent_chats(1, limit=-1) == []

        # SQLite treats LIMIT -1 as unlimited, so the clamped value sent to
        # SQL must be non-negative.
        assert db.last_fetch_all_params is not None
        assert db.last_fetch_all_params[-1] == 0

    def test_history_reads_tuple_count_row_and_evicts(self, monkeypatch):
        db = _TupleLastChatDB(one=None, count=10)
        service = _make_service(db, monkeypatch)

        service._add_to_history(1, 2)

        assert any("DELETE FROM user_recent_chats" in q for q, _ in db.executed)
        assert any("INSERT INTO user_recent_chats" in q for q, _ in db.executed)

    def test_history_skips_evict_below_cap_tuple_count(self, monkeypatch):
        db = _TupleLastChatDB(one=None, count=3)
        service = _make_service(db, monkeypatch)

        service._add_to_history(1, 2)

        assert not any("DELETE FROM user_recent_chats" in q for q, _ in db.executed)
        assert any("INSERT INTO user_recent_chats" in q for q, _ in db.executed)
