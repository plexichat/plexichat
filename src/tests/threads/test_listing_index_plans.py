"""Channel thread listings must be index-backed, not sort-in-a-temp-B-tree.

The active/archived listing queries filter on ``(channel_id, state, deleted)``
and then order by a fourth column. With only single-column indexes SQLite had
to materialise every matching row into a temporary B-tree before sorting,
which dominated the latency of ``GET /channels/{id}/threads`` and
``GET /channels/{id}/threads/archived``. The composite covering indexes in
``thread_threads`` let the equality filters *and* the ORDER BY resolve from a
single index scan. These tests run EXPLAIN QUERY PLAN over the real schema so
a removed or malformed index fails loudly.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core.database.core.schema_splitter import split_sql_statements
from src.core.threads.schema import SCHEMA

pytestmark = pytest.mark.unit

_FROM = (
    "SELECT t.*, m.user_id AS membership_user_id "
    "FROM thread_threads t "
    "LEFT JOIN thread_members m ON m.thread_id = t.id AND m.user_id = ? "
    "WHERE t.channel_id = ? AND t.state = ? AND t.deleted = 0 "
)
_ACTIVE = _FROM + "ORDER BY t.last_message_at DESC NULLS LAST"
_ARCHIVED = _FROM + "ORDER BY t.archived_at DESC, t.id DESC LIMIT ?"
_ARCHIVED_BEFORE = (
    _FROM + "AND t.archived_at < ? ORDER BY t.archived_at DESC, t.id DESC LIMIT ?"
)


def _schema_statements() -> list[str]:
    statements = []
    for stmt in split_sql_statements(SCHEMA):
        body = "\n".join(
            line for line in stmt.splitlines() if not line.strip().startswith("--")
        ).strip()
        if body:
            statements.append(body)
    return statements


def _thread_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for stmt in _schema_statements():
        conn.execute(stmt)
    return conn


def _plan(sql: str, params: tuple) -> str:
    conn = _thread_db()
    try:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    finally:
        conn.close()
    return " | ".join(row[3] for row in rows)


def test_schema_declares_both_listing_indexes():
    assert "idx_thread_active_listing" in SCHEMA
    assert "idx_thread_archived_listing" in SCHEMA


def test_active_listing_uses_covering_index_without_temp_sort():
    plan = _plan(_ACTIVE, (42, 1, "active"))
    assert "idx_thread_active_listing" in plan, plan
    assert "TEMP B-TREE" not in plan.upper(), plan


def test_archived_listing_uses_covering_index_without_temp_sort():
    plan = _plan(_ARCHIVED, (42, 1, "archived", 50))
    assert "idx_thread_archived_listing" in plan, plan
    assert "TEMP B-TREE" not in plan.upper(), plan


def test_archived_before_cursor_uses_covering_index_without_temp_sort():
    plan = _plan(_ARCHIVED_BEFORE, (42, 1, "archived", 9_999_999, 50))
    assert "idx_thread_archived_listing" in plan, plan
    assert "TEMP B-TREE" not in plan.upper(), plan
