"""Regression: participant bulk insertion must be bounded, not one INSERT per row.

``ParticipantRepository.create_bulk`` used to issue a separate
``INSERT OR IGNORE`` (and statement round-trip) for every participant. Adding a
single user to many conversations at once — its real caller,
``add_user_to_multiple_conversations`` — therefore grew linearly in the number
of rows. The rows are now folded into bounded ``executemany`` chunks.

This test proves both halves against a self-contained counting DB:

* the legacy per-row loop emits exactly one statement per row (N), while
  ``create_bulk`` emits ``ceil(N / chunk)`` bounded statements;
* the resulting rows are byte-for-byte identical, including ``INSERT OR IGNORE``
  deduplication when two rows share ``(conversation_id, user_id)``.
"""

import sqlite3

import pytest

from src.core.messaging.models import ParticipantRole
from src.core.messaging.repositories.participant import (
    _MAX_BULK_CHUNK,
    ParticipantRepository,
)

pytestmark = [pytest.mark.unit, pytest.mark.messaging]

_INSERT = """INSERT OR IGNORE INTO msg_participants
             (id, conversation_id, user_id, role, joined_at)
             VALUES (?, ?, ?, ?, ?)"""


class _CountingDB:
    """A real sqlite3 store that records every statement it is asked to run."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE msg_participants ("
            "id INTEGER PRIMARY KEY, "
            "conversation_id INTEGER NOT NULL, "
            "user_id INTEGER NOT NULL, "
            "role TEXT NOT NULL, "
            "joined_at INTEGER NOT NULL, "
            "UNIQUE(conversation_id, user_id))"
        )
        self.statements = []
        self.in_transaction = False

    # -- transaction surface the repository relies on -----------------------
    def begin_transaction(self):
        self.in_transaction = True
        self.conn.execute("BEGIN")

    def commit(self):
        self.in_transaction = False
        self.conn.commit()

    def rollback(self):
        self.in_transaction = False
        self.conn.rollback()

    # -- statement surface (the only calls that count as "queries") ---------
    def execute(self, sql, params=(), auto_commit=True):
        self.statements.append(("execute", sql, tuple(params)))
        return self.conn.execute(sql, params)

    def execute_many(self, sql, params_list, auto_commit=True):
        self.statements.append(("execute_many", sql, [tuple(p) for p in params_list]))
        return self.conn.executemany(sql, params_list)

    # -- read helper ---------------------------------------------------------
    def rows(self):
        cur = self.conn.execute(
            "SELECT id, conversation_id, user_id, role, joined_at "
            "FROM msg_participants ORDER BY id"
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _row(i):
    """Unique (conversation_id, user_id) so no two rows collide by default."""
    return {
        "id": i,
        "conversation_id": 10_000 + i,
        "user_id": 20_000 + i,
        "role": ParticipantRole.MEMBER,
        "joined_at": 1_000 + i,
    }


def _legacy_per_row(db, rows):
    """The exact shape of the old loop, kept here only to measure 'before'."""
    db.begin_transaction()
    for p in rows:
        db.execute(
            _INSERT,
            (
                p["id"],
                p["conversation_id"],
                p["user_id"],
                p["role"].value,
                p["joined_at"],
            ),
            auto_commit=False,
        )
    db.commit()


def test_create_bulk_query_count_is_bounded_not_per_row():
    n = 3 * _MAX_BULK_CHUNK + 7  # deliberately spans several chunks
    rows = [_row(i) for i in range(1, n + 1)]

    before_db = _CountingDB()
    _legacy_per_row(before_db, rows)
    before = len(before_db.statements)

    after_db = _CountingDB()
    ParticipantRepository(after_db).create_bulk([dict(r) for r in rows])
    after = len(after_db.statements)

    # Before: one statement per row. After: one bounded executemany per chunk.
    assert before == n
    chunks = (n + _MAX_BULK_CHUNK - 1) // _MAX_BULK_CHUNK
    assert after == chunks
    assert after <= 4
    assert after * 20 < before, (
        f"expected order-of-magnitude reduction: {before} -> {after}"
    )

    # Every emitted statement stays bounded and keeps the conflict handling.
    for kind, sql, params in after_db.statements:
        assert kind == "execute_many"
        assert "INSERT OR IGNORE INTO msg_participants" in sql
        assert 0 < len(params) <= _MAX_BULK_CHUNK
        assert all(len(p) == 5 for p in params)


def test_create_bulk_matches_per_row_rows_and_dedupes_conflicts():
    rows = [
        {
            "id": 1,
            "conversation_id": 100,
            "user_id": 1001,
            "role": ParticipantRole.OWNER,
            "joined_at": 10,
        },
        {
            "id": 2,
            "conversation_id": 100,
            "user_id": 1002,
            "role": ParticipantRole.MEMBER,
            "joined_at": 11,
        },
        # Duplicate (conversation_id, user_id) of row 2: INSERT OR IGNORE must
        # drop it even though its primary key differs.
        {
            "id": 3,
            "conversation_id": 100,
            "user_id": 1002,
            "role": ParticipantRole.MEMBER,
            "joined_at": 12,
        },
        {
            "id": 4,
            "conversation_id": 200,
            "user_id": 1001,
            "role": ParticipantRole.ADMIN,
            "joined_at": 13,
        },
    ]

    legacy_db = _CountingDB()
    _legacy_per_row(legacy_db, rows)

    bulk_db = _CountingDB()
    ParticipantRepository(bulk_db).create_bulk([dict(r) for r in rows])

    assert bulk_db.rows() == legacy_db.rows()
    # The duplicate was ignored; the three distinct rows survive with exact data.
    assert bulk_db.rows() == [
        {
            "id": 1,
            "conversation_id": 100,
            "user_id": 1001,
            "role": "owner",
            "joined_at": 10,
        },
        {
            "id": 2,
            "conversation_id": 100,
            "user_id": 1002,
            "role": "member",
            "joined_at": 11,
        },
        {
            "id": 4,
            "conversation_id": 200,
            "user_id": 1001,
            "role": "admin",
            "joined_at": 13,
        },
    ]
