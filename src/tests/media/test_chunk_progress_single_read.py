"""Chunk progress records one session read per attempt, not two.

``_record_chunk_progress`` used to SELECT the mutable progress columns and then
issue a *second* SELECT for ``total_chunks`` on every chunk write (and again on
every optimistic-concurrency retry). ``total_chunks`` is immutable for a
session, so the second round-trip was pure overhead on the upload hot path.

The regression guards both halves of the fix:

* exactly one ``fetch_one`` per attempt (the perf win);
* completion is still decided from the session's real ``total_chunks``, so a
  multi-chunk upload cannot be marked complete after a single chunk.
"""

from __future__ import annotations

import pytest

from src.core.media.chunked import ChunkedUploadManager

pytestmark = pytest.mark.unit


class _Cursor:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount

    def close(self) -> None:
        pass


class _FakeDB:
    """Return queued progress rows; count SELECTs and UPDATE rowcounts."""

    def __init__(self, rows, rowcounts=None) -> None:
        self._rows = list(rows)
        self._rowcounts = list(rowcounts or [1])
        self.selects = []
        self.updates = 0

    def fetch_one(self, sql, params=None):
        self.selects.append(" ".join(sql.split()))
        return self._rows.pop(0) if self._rows else None

    def execute(self, sql, params=None):
        self.updates += 1
        return _Cursor(self._rowcounts[self.updates - 1])


def _manager(db):
    # Avoid __init__ (config load / temp-dir creation); only _db is used here.
    manager = object.__new__(ChunkedUploadManager)
    manager._db = db
    return manager


def test_progress_reads_total_chunks_in_the_same_select():
    row = {
        "uploaded_chunks": 1,
        "uploaded_bytes": 10,
        "received_chunks": "[0]",
        "updated_at": 123,
        "total_chunks": 4,
    }
    db = _FakeDB([row])

    result = _manager(db)._record_chunk_progress("session", 1, 5, 456)

    # Two received chunks of four: not complete, byte total accumulated.
    assert result == (2, 15, False)
    # Before the fix this was 2 SELECTs (progress row + total_chunks row).
    assert len(db.selects) == 1, db.selects
    assert "total_chunks" in db.selects[0]
    assert "FROM media_upload_sessions" in db.selects[0]
    assert db.updates == 1


def test_progress_is_complete_only_at_the_real_total_tuple_row():
    # Tuple adapters use SELECT order: uploaded_chunks, uploaded_bytes,
    # received_chunks, updated_at, total_chunks.
    row = (1, 10, "[0]", 123, 2)
    db = _FakeDB([row])

    result = _manager(db)._record_chunk_progress("session", 1, 5, 456)

    assert result == (2, 15, True)
    assert len(db.selects) == 1, db.selects


def test_optimistic_retry_reads_once_per_attempt():
    first = {
        "uploaded_chunks": 0,
        "uploaded_bytes": 0,
        "received_chunks": "[]",
        "updated_at": 1,
        "total_chunks": 3,
    }
    second = dict(first, updated_at=2, uploaded_chunks=1, received_chunks="[7]")
    db = _FakeDB([first, second], rowcounts=[0, 1])

    result = _manager(db)._record_chunk_progress("session", 1, 4, 789)

    assert result == (2, 4, False)
    assert db.updates == 2
    # One read per attempt, never a separate total_chunks probe.
    assert len(db.selects) == 2, db.selects
