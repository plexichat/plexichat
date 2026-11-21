"""The pins listing must bound its storage read regardless of total pin count.

``PinRepository.get_pinned_messages`` used to have no ``LIMIT``: a conversation
with a very large pin set made the cold read fetch and hydrate every pin into a
Message model, only for the API route's ``[:limit]`` slice to discard all but
one page. The read is now bounded at the storage layer (same ceiling as the
route), while the participant gate stays in the service and the newest-first
order is preserved so the response is unchanged.
"""

import sqlite3

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError
from src.core.messaging.repositories.pin import MAX_PIN_FETCH, PinRepository
from src.core.messaging.schema import SCHEMA_STATEMENTS
from src.core.messaging.services.pin import PinService

pytestmark = pytest.mark.messaging

_CONV = 5000
_USER = 42


class _CountingDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.fetch_all_calls = []

    def fetch_all(self, query, params=None):
        self.fetch_all_calls.append((" ".join(query.split()), tuple(params or ())))
        return self.rows

    def fetch_one(self, query, params=None):
        return None


def test_pin_read_is_one_bounded_query():
    db = _CountingDB()
    PinRepository(db).get_pinned_messages(_CONV)

    assert len(db.fetch_all_calls) == 1
    sql, params = db.fetch_all_calls[0]
    assert "LIMIT ?" in sql
    assert "ORDER BY p.pinned_at DESC" in sql
    assert params == (_CONV, MAX_PIN_FETCH)


@pytest.mark.parametrize(
    "requested,expected",
    [
        (7, 7),
        (0, 1),  # never unlimited / negative
        (-1, 1),  # SQLite treats LIMIT -1 as *unlimited*
        (10**9, MAX_PIN_FETCH),
    ],
)
def test_pin_read_limit_is_bounded(requested, expected):
    db = _CountingDB()
    PinRepository(db).get_pinned_messages(_CONV, limit=requested)

    assert db.fetch_all_calls[0][1] == (_CONV, expected)


class _Participant:
    def __init__(self, allowed):
        self.allowed = allowed

    def is_participant(self, conversation_id, user_id):
        return self.allowed


class _Repo:
    def __init__(self):
        self.calls = []

    def get_pinned_messages(self, conversation_id, limit=MAX_PIN_FETCH):
        self.calls.append((conversation_id, limit))
        return []


def _service(allowed):
    svc = PinService(_CountingDB(), _Participant(allowed))
    repo = _Repo()
    svc._repo = repo
    return svc, repo


def test_participant_gate_runs_before_bounded_read():
    svc, repo = _service(allowed=False)

    with pytest.raises(ConversationAccessDeniedError):
        svc.get_pinned_messages(_USER, _CONV)

    assert repo.calls == []


def test_service_threads_limit_to_bounded_read():
    svc, repo = _service(allowed=True)
    svc.get_pinned_messages(_USER, _CONV, limit=9)

    assert repo.calls == [(_CONV, 9)]


class _SQLiteDB:
    """Minimal DB adapter returning dict rows for the repository."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        for stmt in SCHEMA_STATEMENTS:
            self.conn.execute(stmt)

    def fetch_all(self, query, params=None):
        return [dict(r) for r in self.conn.execute(query, params or ())]

    def fetch_one(self, query, params=None):
        row = self.conn.execute(query, params or ()).fetchone()
        return dict(row) if row is not None else None


def test_real_db_read_is_bounded_and_keeps_newest_first():
    db = _SQLiteDB()
    now = 1_700_000_000_000
    total = MAX_PIN_FETCH + 25
    db.conn.execute(
        "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) "
        "VALUES (?,?,?,?)",
        (_CONV, "dm", now, now),
    )
    db.conn.executemany(
        "INSERT INTO msg_messages "
        "(id, conversation_id, author_id, content, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        [(100000 + i, _CONV, _USER, "m", now, now) for i in range(total)],
    )
    db.conn.executemany(
        "INSERT INTO msg_pinned (id, conversation_id, message_id, pinned_by, pinned_at) "
        "VALUES (?,?,?,?,?)",
        [(900000 + i, _CONV, 100000 + i, _USER, now + i) for i in range(total)],
    )
    db.conn.commit()

    rows = PinRepository(db).get_pinned_messages(_CONV)

    assert len(rows) == MAX_PIN_FETCH  # not `total`
    assert [r["id"] for r in rows] == [
        100000 + i for i in range(total - 1, total - 1 - MAX_PIN_FETCH, -1)
    ]
    assert all(
        rows[i]["pinned_at"] > rows[i + 1]["pinned_at"] for i in range(len(rows) - 1)
    )
