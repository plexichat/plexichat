"""Row-shape tests for the pg8000 PostgreSQL engine.

pg8000 returns bare tuples. Both drivers it replaced returned rows that also
support positional access, column access, ``keys()`` and ``dict(row)`` —
``sqlite3.Row`` on SQLite and psycopg's ``DictRow`` on PostgreSQL — and call
sites that read *raw* cursors (voice, push, servers, profiles, antispam) rely on
exactly that.

These tests pin the adapter that restores the contract. They need no live
PostgreSQL, which matters because the engine's only other coverage of this
behaviour is integration-only.
"""

from __future__ import annotations

from src.core.database.engines.postgres import (
    DictRow,
    _Pg8000Connection,
    _Pg8000Cursor,
)


class _FakeCursor:
    """Minimal stand-in for a pg8000 cursor: tuple rows, DB-API surface."""

    def __init__(self, rows, columns=("id", "name")):
        self._rows = list(rows)
        self.description = [(c, 23, None, None, None, None, None) for c in columns]
        self.rowcount = -1
        self.executed = []

    def execute(self, query, params=()):
        self.executed.append((query, params))
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, size=None):
        n = size if size is not None else 1
        taken, self._rows = self._rows[:n], self._rows[n:]
        return taken

    def close(self):
        pass


def test_dict_row_supports_mapping_and_sequence_access():
    row = DictRow((7, "alice"), ["id", "name"])

    # Column access (the form ``fetch_one`` consumers use).
    assert row["id"] == 7
    assert row["name"] == "alice"
    # Positional access (locking.py and several raw-cursor call sites).
    assert row[0] == 7
    assert row[1] == "alice"
    # Both conversions the codebase uses.
    assert dict(row) == {"id": 7, "name": "alice"}
    assert tuple(row) == (7, "alice")
    assert len(row) == 2
    assert row.keys() == ["id", "name"]
    assert row.values() == [7, "alice"]
    assert row.items() == [("id", 7), ("name", "alice")]
    assert row.get("id") == 7
    assert row.get("missing") is None
    assert row.get("missing", "fallback") == "fallback"


def test_dict_row_collapses_duplicate_columns_like_the_drivers():
    row = DictRow((1, 2), ["v", "v"])
    assert row["v"] == 2
    assert row[0] == 1


def test_cursor_adapter_wraps_fetched_rows():
    cursor = _Pg8000Cursor(_FakeCursor([(1, "a"), (2, "b")]))

    first = cursor.fetchone()
    assert isinstance(first, DictRow)
    assert first["name"] == "a"

    rest = cursor.fetchall()
    assert [row["id"] for row in rest] == [2]
    assert all(isinstance(row, DictRow) for row in rest)


def test_cursor_adapter_wraps_fetchmany():
    cursor = _Pg8000Cursor(_FakeCursor([(1, "a"), (2, "b")]))
    rows = cursor.fetchmany(1)
    assert [row["id"] for row in rows] == [1]


def test_cursor_adapter_handles_empty_results():
    cursor = _Pg8000Cursor(_FakeCursor([]))
    assert cursor.fetchone() is None
    assert cursor.fetchall() == []


def test_cursor_adapter_delegates_execute_rowcount_and_description():
    fake = _FakeCursor([(1, "a")])
    cursor = _Pg8000Cursor(fake)

    cursor.execute("SELECT 1", ())
    assert fake.executed == [("SELECT 1", ())]
    assert cursor.rowcount == 1
    assert cursor.description is fake.description


def test_cursor_adapter_passes_mapping_rows_through_untouched():
    """A row that already supports column access must not be rewrapped."""

    class _MappingCursor(_FakeCursor):
        def fetchall(self):
            return [{"id": 1}]

    assert _Pg8000Cursor(_MappingCursor([])).fetchall() == [{"id": 1}]


def test_connection_cursor_returns_the_wrapping_adapter():
    class _FakeConnection:
        def cursor(self):
            return _FakeCursor([(1, "a")])

    cursor = _Pg8000Connection(_FakeConnection()).cursor()
    assert isinstance(cursor, _Pg8000Cursor)
    assert cursor.fetchone()["name"] == "a"


def test_connection_closed_and_broken_track_the_driver_socket():
    """pg8000 has no ``closed``/``broken``; the adapter derives both."""

    class _FakeConnection:
        _usock: object | None = object()

        def close(self):
            self._usock = None

    conn = _Pg8000Connection(_FakeConnection())
    assert conn.closed is False
    assert conn.broken is False

    conn.close()
    assert conn.closed is True
    assert conn.broken is True
