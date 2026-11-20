"""Regression: batched message indexing keeps per-row write semantics.

``SQLiteFTS5Indexer.index_messages_batch`` used to call ``index_message`` once
per message, paying a DELETE + INSERT round trip (connection checkout,
monitoring, commit) for every row. Reindex feeds it thousands of messages, so
those round trips dominated wall time. The batch path now collapses them into
one ``execute_many`` for the DELETEs and one for the INSERTs.

These tests pin the observable behaviour that must not change: one row per id
holding the latest content (including duplicate ids in a single batch),
identical field conversion to the single-row path, an empty-batch no-op, and a
per-row fallback when the backend refuses a batched statement. They also
assert the round-trip reduction itself, so a regression back to per-row writes
fails loudly.
"""

import sqlite3

import pytest

from src.core.search.indexer.sqlite_fts import SQLiteFTS5Indexer
from src.core.search.models import IndexedMessage

pytestmark = [pytest.mark.unit, pytest.mark.search]


class CountingDB:
    """sqlite3 wrapper exposing the Database API subset the indexer uses.

    Records every write so the tests can assert round-trip counts, and can be
    told to reject ``execute_many`` to exercise the fallback path.
    """

    type = "sqlite"

    def __init__(self, fail_execute_many=False):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.executes = []
        self.execute_manies = []
        self.fail_execute_many = fail_execute_many

    def reset_calls(self):
        self.executes.clear()
        self.execute_manies.clear()

    def execute(self, sql, params=None):
        self.executes.append((sql, params))
        cur = self.conn.execute(sql, params or ())
        self.conn.commit()
        return cur

    def execute_many(self, sql, params_list, auto_commit=True):
        self.execute_manies.append((sql, list(params_list)))
        if self.fail_execute_many:
            raise RuntimeError("simulated batched-write failure")
        self.conn.executemany(sql, params_list)
        if auto_commit:
            self.conn.commit()

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self.conn.execute(sql, params or ()).fetchall()]

    def fetch_one(self, sql, params=None):
        row = self.conn.execute(sql, params or ()).fetchone()
        return dict(row) if row is not None else None

    def rows(self):
        return self.conn.execute(
            "SELECT message_id, content, author_id, author_username, "
            "conversation_id, server_id, channel_id, created_at, "
            "has_attachments, has_embeds, has_links, mentions, is_pinned, "
            "attachment_types FROM search_messages_fts"
        ).fetchall()


def _message(message_id, content, **overrides):
    base = dict(
        message_id=message_id,
        content=content,
        author_id=42,
        conversation_id=7,
    )
    base.update(overrides)
    return IndexedMessage(**base)


def _indexer(db=None):
    db = db or CountingDB()
    indexer = SQLiteFTS5Indexer(db)
    assert indexer.initialize() is True
    db.reset_calls()  # ignore schema setup writes
    return indexer, db


def test_batch_uses_executemany_and_preserves_rows():
    indexer, db = _indexer()
    messages = [
        _message(i, f"regressionuniqueword{i}", created_at=1699999999000 + i)
        for i in range(1, 6)
    ]

    assert indexer.index_messages_batch(messages) == 5

    # One batched DELETE and one batched INSERT, no per-row execute calls.
    assert len(db.execute_manies) == 2
    assert [len(params) for _sql, params in db.execute_manies] == [5, 5]
    assert db.executes == []

    rows = db.rows()
    assert len(rows) == 5
    assert {row["message_id"] for row in rows} == {str(i) for i in range(1, 6)}

    # The documents are actually searchable end-to-end.
    hits = indexer.search_messages("regressionuniqueword3")
    assert [str(h.message_id) for h in hits] == ["3"]


def test_batch_update_replaces_content_without_duplicate_rows():
    indexer, db = _indexer()

    assert indexer.index_messages_batch([_message(1, "oldversion")]) == 1
    assert indexer.index_messages_batch([_message(1, "newversion")]) == 1

    rows = db.rows()
    assert len(rows) == 1, "re-indexing an id must not leave a stale row"
    assert rows[0]["content"] == "newversion"


def test_duplicate_ids_in_one_batch_keep_last_document():
    indexer, db = _indexer()
    batch = [_message(1, "firstdocument"), _message(1, "seconddocument")]

    assert indexer.index_messages_batch(batch) == len(batch)

    rows = db.rows()
    assert len(rows) == 1
    assert rows[0]["message_id"] == "1"
    assert rows[0]["content"] == "seconddocument"


def test_empty_batch_is_a_noop():
    indexer, db = _indexer()

    assert indexer.index_messages_batch([]) == 0
    assert db.executes == []
    assert db.execute_manies == []


def test_batch_falls_back_to_per_row_when_executemany_fails():
    db = CountingDB(fail_execute_many=True)
    indexer, db = _indexer(db)
    messages = [_message(i, f"fallbackword{i}") for i in range(1, 5)]

    # The batched DELETE raises; the indexer must still index every message
    # through the original per-row path and report the same count.
    assert indexer.index_messages_batch(messages) == 4
    assert len(db.execute_manies) == 1  # attempted, then abandoned
    assert len(db.executes) >= 8  # DELETE + INSERT for each of 4 messages
    assert len(db.rows()) == 4


def test_batch_and_single_row_write_identical_values():
    indexer, db = _indexer()
    common = dict(
        author_id=42,
        author_username="author_42",
        conversation_id=7,
        server_id=None,
        channel_id=None,
        created_at=1699999999000,
        has_attachments=True,
        attachment_types=["image", "file"],
        has_embeds=True,
        has_links=False,
        mentions=[101, 102],
        is_pinned=True,
    )
    single = _message(11, "identicalcontent", **common)
    batched = _message(12, "identicalcontent", **common)

    assert indexer.index_message(single) is True
    assert indexer.index_messages_batch([batched]) == 1

    rows = {row["message_id"]: row for row in db.rows()}
    single_row = dict(rows["11"])
    batch_row = dict(rows["12"])
    del single_row["message_id"]
    del batch_row["message_id"]
    assert single_row == batch_row
