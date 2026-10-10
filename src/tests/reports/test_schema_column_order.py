"""Pin the physical column order behind the tuple-row mappers.

``_row_to_message_report`` / ``_row_to_user_report`` zip positional rows
against ``MESSAGE_REPORT_COLUMNS`` / ``USER_REPORT_COLUMNS``. Those lists
must match the real ``CREATE TABLE`` order in ``schema.py`` exactly: any
reorder or mid-table column add would otherwise silently mis-map fields
(the F-BUG-1 class). This test builds the genuine schema in SQLite and
compares ``PRAGMA table_info`` order against the mapper constants, plus a
round-trip that writes a full row and reads it back positionally.
"""

from __future__ import annotations

import sqlite3

from src.core.reports import (
    MESSAGE_REPORT_COLUMNS,
    USER_REPORT_COLUMNS,
    _row_to_message_report,
    _row_to_user_report,
)
from src.core.reports.schema import SCHEMA


def _schema_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    return conn


def _pragma_order(conn: sqlite3.Connection, table: str) -> list:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in sorted(rows, key=lambda r: r[0])]


def test_message_report_columns_match_schema():
    assert _pragma_order(_schema_db(), "message_reports") == list(
        MESSAGE_REPORT_COLUMNS
    )


def test_user_report_columns_match_schema():
    assert _pragma_order(_schema_db(), "user_reports") == list(USER_REPORT_COLUMNS)


def test_message_report_positional_round_trip():
    conn = _schema_db()
    values = (
        1,
        100,
        200,
        300,
        400,
        500,
        "spam",
        "spam",
        "details",
        "content",
        "http://e",
        "high",
        "pending",
        12345,
        111,
        7,
        "notes",
        "warned",
        9,
        222,
        "fixed",
    )
    assert len(values) == len(MESSAGE_REPORT_COLUMNS)
    placeholders = ", ".join("?" * len(values))
    conn.execute(f"INSERT INTO message_reports VALUES ({placeholders})", values)
    row = conn.execute("SELECT * FROM message_reports").fetchone()
    report = _row_to_message_report(tuple(row))
    assert report.id == 1
    assert report.message_id == 100
    assert report.reported_user_id == 500
    assert report.evidence_urls == "http://e"
    assert report.priority == "high"
    assert report.reported_at == 12345
    assert report.reviewed_by == 7
    assert report.action_taken == "warned"
    assert report.escalated_at == 222
    assert report.resolution == "fixed"


def test_user_report_positional_round_trip():
    conn = _schema_db()
    values = (
        1,
        500,
        400,
        "harassment",
        "harassment",
        "details",
        "10,20",
        "http://e",
        "high",
        "pending",
        54321,
        111,
        7,
        "notes",
        "banned",
        9,
        222,
        "fixed",
    )
    assert len(values) == len(USER_REPORT_COLUMNS)
    placeholders = ", ".join("?" * len(values))
    conn.execute(f"INSERT INTO user_reports VALUES ({placeholders})", values)
    row = conn.execute("SELECT * FROM user_reports").fetchone()
    report = _row_to_user_report(tuple(row))
    assert report.reported_user_id == 500
    assert report.evidence_message_ids == [10, 20]
    assert report.reported_at == 54321
    assert report.action_taken == "banned"
    assert report.resolution == "fixed"
