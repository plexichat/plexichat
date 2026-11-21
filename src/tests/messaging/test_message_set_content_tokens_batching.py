"""Regression: content-token replacement must be bounded, not one INSERT per token.

``MessageRepository.set_content_tokens`` used to issue a separate
``INSERT OR IGNORE`` for every token index. A long message paid N statement
round-trips on every send/edit. The inserts are now folded into bounded
``executemany`` chunks.

The counting DB proves:

* the legacy loop emits 1 DELETE + N INSERT statements, while the new path
  emits 1 DELETE + ``ceil(N / chunk)`` ``execute_many`` calls;
* the resulting rows are byte-for-byte identical, including duplicate token
  values, the ``auto_commit`` passthrough, and ``INSERT OR IGNORE`` behaviour
  when a position collides with a concurrently inserted row.
"""

import sqlite3

import pytest

from src.core.messaging.repositories.message import (
    _MAX_BULK_CHUNK,
    MessageRepository,
)

pytestmark = [pytest.mark.unit, pytest.mark.messaging]

_DELETE = "DELETE FROM msg_message_tokens WHERE message_id = ?"
_INSERT = (
    "INSERT OR IGNORE INTO msg_message_tokens "
    "(message_id, token_index, position) VALUES (?, ?, ?)"
)


class _CountingDB:
    """A real sqlite3 store that records every statement it is asked to run."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE msg_message_tokens ("
            "message_id INTEGER NOT NULL, "
            "token_index TEXT NOT NULL, "
            "position INTEGER NOT NULL, "
            "PRIMARY KEY (message_id, position))"
        )
        self.statements = []
        # When set, a DELETE injects a token at position 0 (simulating a
        # concurrent writer) before the inserts run, exercising OR IGNORE.
        self.conflict_token = None

    def execute(self, sql, params=(), auto_commit=True):
        self.statements.append(("execute", sql, tuple(params), auto_commit))
        cursor = self.conn.execute(sql, params)
        if "DELETE FROM msg_message_tokens" in sql and self.conflict_token:
            self.conn.execute(
                _INSERT, (params[0], self.conflict_token, 0)
            )
        return cursor

    def execute_many(self, sql, params_list, auto_commit=True):
        self.statements.append(
            ("execute_many", sql, [tuple(p) for p in params_list], auto_commit)
        )
        return self.conn.executemany(sql, params_list)

    def rows(self):
        cur = self.conn.execute(
            "SELECT message_id, token_index, position FROM msg_message_tokens "
            "ORDER BY position"
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _legacy(db, msg_id, token_indexes, auto_commit=True):
    """The exact shape of the old loop, kept here only to measure 'before'."""
    db.execute(_DELETE, (msg_id,), auto_commit=auto_commit)
    for position, token_index in enumerate(token_indexes):
        db.execute(
            _INSERT, (msg_id, token_index, position), auto_commit=auto_commit
        )


def test_set_content_tokens_query_count_is_bounded_not_per_token():
    n = 3 * _MAX_BULK_CHUNK + 7  # deliberately spans several chunks
    tokens = [f"tok{i}" for i in range(n)]

    before_db = _CountingDB()
    _legacy(before_db, 42, tokens)
    before = len(before_db.statements)

    after_db = _CountingDB()
    MessageRepository(after_db).set_content_tokens(42, tokens)
    after = len(after_db.statements)

    assert before == 1 + n
    chunks = (n + _MAX_BULK_CHUNK - 1) // _MAX_BULK_CHUNK
    assert after == 1 + chunks
    assert after <= 5
    assert after * 20 < before, f"expected order-of-magnitude reduction: {before} -> {after}"

    # First statement is still the scoped DELETE; the rest are bounded, ordered
    # executemany batches that retain the OR IGNORE conflict handling.
    assert before_db.statements[0][0] == "execute"
    assert "DELETE FROM msg_message_tokens WHERE message_id = ?" in after_db.statements[0][1]
    expected_start = 0
    for kind, sql, params, _ac in after_db.statements[1:]:
        assert kind == "execute_many"
        assert "INSERT OR IGNORE INTO msg_message_tokens" in sql
        assert 0 < len(params) <= _MAX_BULK_CHUNK
        assert all(len(p) == 3 for p in params)
        assert [p[2] for p in params] == list(
            range(expected_start, expected_start + len(params))
        )
        expected_start += len(params)
    assert expected_start == n


def test_set_content_tokens_matches_legacy_rows_and_preserves_duplicates():
    # Repeated token values at different positions: the PK is (message_id,
    # position), so duplicates must survive -- no over-dedup by token value.
    tokens = ["alpha", "beta", "alpha", "gamma", "beta"]

    legacy_db = _CountingDB()
    _legacy(legacy_db, 7, tokens)

    bulk_db = _CountingDB()
    MessageRepository(bulk_db).set_content_tokens(7, tokens)

    assert bulk_db.rows() == legacy_db.rows() == [
        {"message_id": 7, "token_index": "alpha", "position": 0},
        {"message_id": 7, "token_index": "beta", "position": 1},
        {"message_id": 7, "token_index": "alpha", "position": 2},
        {"message_id": 7, "token_index": "gamma", "position": 3},
        {"message_id": 7, "token_index": "beta", "position": 4},
    ]


def test_set_content_tokens_preserves_or_ignore_and_auto_commit_flag():
    tokens = ["a", "b", "c"]
    for auto_commit in (True, False):
        legacy_db = _CountingDB()
        legacy_db.conflict_token = "CONCURRENT"
        _legacy(legacy_db, 9, tokens, auto_commit=auto_commit)

        bulk_db = _CountingDB()
        bulk_db.conflict_token = "CONCURRENT"
        MessageRepository(bulk_db).set_content_tokens(
            9, tokens, auto_commit=auto_commit
        )

        # Position 0 collided with the concurrent row; OR IGNORE must swallow
        # it in both paths rather than raising, yielding identical rows.
        assert bulk_db.rows() == legacy_db.rows() == [
            {"message_id": 9, "token_index": "CONCURRENT", "position": 0},
            {"message_id": 9, "token_index": "b", "position": 1},
            {"message_id": 9, "token_index": "c", "position": 2},
        ]
        # The caller-chosen transaction boundary is passed to every statement.
        assert all(call[3] is auto_commit for call in bulk_db.statements)
