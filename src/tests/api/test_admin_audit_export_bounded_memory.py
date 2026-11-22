"""Regression: admin audit export must stream in bounded DB pages.

The old exporter issued one ``fetch_all`` for the whole (capped) result set,
built the entire CSV in a ``StringIO`` and handed the whole string to
``StreamingResponse(iter([...]))``. Peak memory therefore grew with the export
size. This test exercises the route against a real SQLite table and asserts
that reads are keyset-paginated in bounded slices, that the full result is still
emitted in the right order, and that the global row cap is honoured.

Run with: pytest -n0 src/tests/api/test_admin_audit_export_bounded_memory.py
"""

import csv
import io
import sqlite3
import time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin import audit as audit_routes


class _RecordingSQLiteDB:
    """Thin adapter exposing the ``fetch_all`` surface over a real SQLite DB."""

    def __init__(self, connection):
        self.connection = connection
        self.calls = []
        self.max_page_size = 0

    def fetch_one(self, query, params=None):
        self.calls.append((query, tuple(params or ())))
        cursor = self.connection.execute(query, tuple(params or ()))
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, query, params=None):
        self.calls.append((query, tuple(params or ())))
        cursor = self.connection.execute(query, tuple(params or ()))
        rows = [dict(row) for row in cursor.fetchall()]
        self.max_page_size = max(self.max_page_size, len(rows))
        return rows


def _make_db(row_count, base_ms=None):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE admin_audit_log (
            id INTEGER PRIMARY KEY,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            target_user_id INTEGER,
            details TEXT,
            ip_address TEXT,
            user_agent TEXT,
            status TEXT NOT NULL DEFAULT 'success',
            created_at INTEGER NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX idx_admin_audit_created ON admin_audit_log(created_at)"
    )
    base = base_ms if base_ms is not None else int(time.time() * 1000)
    rows = []
    for i in range(1, row_count + 1):
        # Five events per millisecond: forces the (created_at, id) tiebreak.
        created = base - (i // 5) * 1000
        rows.append(
            (
                i,
                7_000_000_000_000_000 + i,  # snowflake-sized admin id
                "user.update",
                "user",
                8_000_000_000_000_000 + i,
                9_000_000_000_000_000 + i,
                f"details {i}",
                f"10.0.0.{i % 255}",
                f"agent {i}",
                "success",
                created,
            )
        )
    connection.executemany(
        "INSERT INTO admin_audit_log VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    connection.commit()
    return _RecordingSQLiteDB(connection)


def _client(db):
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    patches = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission", return_value=1),
        patch("src.api.get_db", return_value=db),
    )
    for patcher in patches:
        patcher.start()
    return TestClient(app), patches


def _stop(patches):
    for patcher in reversed(patches):
        patcher.stop()


def _expected_ids(db):
    return [
        row["id"]
        for row in db.connection.execute(
            "SELECT id FROM admin_audit_log ORDER BY created_at DESC, id DESC"
        )
    ]


def test_csv_export_streams_in_bounded_pages_and_keeps_order():
    total = audit_routes._AUDIT_EXPORT_CHUNK_SIZE * 3 + 17
    db = _make_db(total)
    client, patches = _client(db)
    try:
        response = client.get("/api/v1/admin/audit/logs/export?format=csv")
        assert response.status_code == 200, response.text
        table = list(csv.reader(io.StringIO(response.text)))
        assert table[0] == audit_routes._CSV_EXPORT_HEADER
        emitted_ids = [int(row[0]) for row in table[1:]]
        assert emitted_ids == _expected_ids(db)
        assert len(emitted_ids) == total
        # Every read is bounded by the page size, never by the full export.
        assert db.max_page_size <= audit_routes._AUDIT_EXPORT_CHUNK_SIZE
        assert len(db.calls) >= 3  # more than one page was consumed
    finally:
        _stop(patches)


def test_json_export_streams_in_bounded_pages_and_stringifies_ids():
    total = audit_routes._AUDIT_EXPORT_CHUNK_SIZE * 2 + 5
    db = _make_db(total)
    client, patches = _client(db)
    try:
        response = client.get("/api/v1/admin/audit/logs/export?format=json")
        assert response.status_code == 200, response.text
        body = response.json()
        assert [row["id"] for row in body["logs"]] == _expected_ids(db)
        first = body["logs"][0]
        assert isinstance(first["admin_id"], str)
        assert isinstance(first["target_id"], str)
        assert isinstance(first["target_user_id"], str)
        assert db.max_page_size <= audit_routes._AUDIT_EXPORT_CHUNK_SIZE
    finally:
        _stop(patches)


def test_export_honours_global_row_cap_across_pages():
    total = audit_routes._MAX_AUDIT_EXPORT_ROWS + 25
    db = _make_db(total)
    client, patches = _client(db)
    try:
        response = client.get("/api/v1/admin/audit/logs/export?format=csv")
        assert response.status_code == 200, response.text
        table = list(csv.reader(io.StringIO(response.text)))
        emitted_ids = [int(row[0]) for row in table[1:]]
        assert len(emitted_ids) == audit_routes._MAX_AUDIT_EXPORT_ROWS
        assert emitted_ids == _expected_ids(db)[: audit_routes._MAX_AUDIT_EXPORT_ROWS]
        assert db.max_page_size <= audit_routes._AUDIT_EXPORT_CHUNK_SIZE
        # Stop reading once the cap is reached instead of draining the table.
        assert (
            len(db.calls)
            <= (
                audit_routes._MAX_AUDIT_EXPORT_ROWS
                // audit_routes._AUDIT_EXPORT_CHUNK_SIZE
            )
            + 1
        )
    finally:
        _stop(patches)
