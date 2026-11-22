"""Regression: attachment bulk insertion must be bounded, not one INSERT per row.

``AttachmentRepository.create_bulk`` used to issue a separate ``INSERT`` (and
statement round-trip) for every attachment. A multi-attachment message made the
statement count grow linearly with the number of rows. The rows are now folded
into bounded ``executemany`` chunks.

The counting DB below proves both halves:

* the legacy per-row loop emits exactly one statement per row (N), while
  ``create_bulk`` emits ``ceil(N / chunk)`` bounded statements;
* the resulting rows are byte-for-byte identical, and the plain (non-
  ``OR IGNORE``) conflict semantics are preserved: a duplicate primary key
  still raises and rolls the entire batch back.
"""

import json
import sqlite3

import pytest

from src.core.messaging.repositories.attachment import (
    _MAX_BULK_CHUNK,
    AttachmentRepository,
)

pytestmark = [pytest.mark.unit, pytest.mark.messaging]

_INSERT = """INSERT INTO msg_attachments
             (id, message_id, filename, content_type, size, url, url_encrypted, created_at, metadata, checksum)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


class _CountingDB:
    """A real sqlite3 store that records every statement it is asked to run."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE msg_attachments ("
            "id INTEGER PRIMARY KEY, "
            "message_id INTEGER NOT NULL, "
            "filename TEXT NOT NULL, "
            "content_type TEXT NOT NULL, "
            "size INTEGER NOT NULL, "
            "url TEXT NOT NULL, "
            "url_encrypted TEXT, "
            "created_at INTEGER NOT NULL, "
            "metadata TEXT, "
            "checksum TEXT, "
            "deleted INTEGER DEFAULT 0)"
        )
        self.statements = []
        self.in_transaction = False
        self.type = "sqlite"

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

    # -- media-reference normalization reads (all empty in this test) -------
    def fetch_one(self, sql, params=()):
        return None

    def fetch_all(self, sql, params=()):
        return []

    def table_exists(self, name):
        return False

    # -- read helper ---------------------------------------------------------
    def rows(self):
        cur = self.conn.execute(
            "SELECT id, message_id, filename, content_type, size, url, "
            "url_encrypted, created_at, metadata, checksum "
            "FROM msg_attachments ORDER BY id"
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _row(i):
    return {
        "id": i,
        "message_id": 5_000 + i,
        "filename": f"file{i}.png",
        "content_type": "image/png",
        "size": 100 + i,
        "url": f"https://cdn.example/file{i}.png",
        "url_encrypted": None,
        "created_at": 1_000 + i,
        "metadata": {"note": f"n{i}"},
        "checksum": f"sha{i}",
    }


def _legacy_per_row(db, rows):
    """The exact shape of the old loop, kept here only to measure 'before'."""
    in_trans = getattr(db, "in_transaction", False)
    if not in_trans:
        db.begin_transaction()
    try:
        for att in rows:
            normalized = dict(att)
            normalized["metadata"] = AttachmentRepository(
                db
            )._reject_locked_media_reference(
                att.get("metadata"),
                att.get("checksum"),
                att.get("filename"),
                att.get("url"),
                att.get("url_encrypted"),
            )
            db.execute(
                _INSERT,
                (
                    normalized["id"],
                    normalized["message_id"],
                    normalized["filename"],
                    normalized["content_type"],
                    normalized["size"],
                    normalized["url"],
                    normalized.get("url_encrypted"),
                    normalized["created_at"],
                    json.dumps(normalized.get("metadata"))
                    if normalized.get("metadata")
                    else None,
                    normalized.get("checksum"),
                ),
                auto_commit=False,
            )
        if not in_trans:
            db.commit()
    except Exception:
        if not in_trans:
            db.rollback()
        raise


def test_create_bulk_query_count_is_bounded_not_per_row():
    n = 3 * _MAX_BULK_CHUNK + 7  # deliberately spans several chunks
    rows = [_row(i) for i in range(1, n + 1)]

    before_db = _CountingDB()
    _legacy_per_row(before_db, rows)
    before = len(before_db.statements)

    after_db = _CountingDB()
    AttachmentRepository(after_db).create_bulk([dict(r) for r in rows])
    after = len(after_db.statements)

    assert before == n
    chunks = (n + _MAX_BULK_CHUNK - 1) // _MAX_BULK_CHUNK
    assert after == chunks
    assert after <= 4
    assert after * 20 < before, (
        f"expected order-of-magnitude reduction: {before} -> {after}"
    )

    for kind, sql, params in after_db.statements:
        assert kind == "execute_many"
        assert "INSERT INTO msg_attachments" in sql
        assert 0 < len(params) <= _MAX_BULK_CHUNK
        assert all(len(p) == 10 for p in params)


def test_create_bulk_matches_per_row_rows_and_keeps_conflict_semantics():
    rows = [_row(i) for i in range(1, 6)]

    legacy_db = _CountingDB()
    _legacy_per_row(legacy_db, rows)

    bulk_db = _CountingDB()
    AttachmentRepository(bulk_db).create_bulk([dict(r) for r in rows])

    assert bulk_db.rows() == legacy_db.rows()
    assert len(bulk_db.rows()) == len(rows)
    assert bulk_db.rows()[0]["metadata"] == json.dumps({"note": "n1"})

    # Plain INSERT (no OR IGNORE): a duplicate primary key must still raise and
    # roll the whole batch back, exactly like the old per-row loop.
    dup = [_row(1), _row(2), dict(_row(1))]
    for db, write in (
        (_CountingDB(), lambda d, r: _legacy_per_row(d, r)),
        (_CountingDB(), lambda d, r: AttachmentRepository(d).create_bulk(r)),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            write(db, dup)
        assert db.rows() == []
