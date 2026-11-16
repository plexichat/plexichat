"""Member-route membership resolution stays single-pass.

The four member-mutation routes used to resolve the ``{member_id}`` path
segment with two sequential queries (first by ``srv_members.id``, then a
fallback by ``user_id``). They now share one disjunctive query. These tests
pin both the reduced call count and the original row precedence so a future
refactor cannot silently reintroduce the extra round-trip or change which
row wins.
"""

import inspect
import sqlite3

from src.api.routes.servers import members
from src.api.routes.servers.members import (
    _resolve_member_user_id,
    _unique_member_user_ids,
)


class _Conn:
    """Minimal ``fetch_one``-style wrapper around an in-memory SQLite DB."""

    def __init__(self, rows):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE srv_members (id INTEGER, server_id INTEGER, user_id INTEGER)"
        )
        self._conn.executemany(
            "INSERT INTO srv_members (id, server_id, user_id) VALUES (?, ?, ?)", rows
        )
        self.calls = []

    def fetch_one(self, sql, params):
        self.calls.append((sql, params))
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None


def test_resolves_by_member_id_in_a_single_query():
    db = _Conn([(10, 1, 100)])
    assert _resolve_member_user_id(db, 1, 10) == {"user_id": 100}
    assert len(db.calls) == 1
    sql, params = db.calls[0]
    assert "OR" in sql and "LIMIT 1" in sql
    assert params == (10, 1, 1, 10, 10)


def test_resolves_by_user_id_in_a_single_query():
    db = _Conn([(10, 1, 100)])
    assert _resolve_member_user_id(db, 1, 100) == {"user_id": 100}
    assert len(db.calls) == 1


def test_member_id_match_keeps_original_precedence():
    # id=100 and user_id=100 both match; the old two-query code preferred the
    # member-id row (user_id=500), so the combined query must as well.
    db = _Conn([(100, 1, 500), (999, 1, 100)])
    assert _resolve_member_user_id(db, 1, 100) == {"user_id": 500}


def test_rows_from_other_servers_are_ignored():
    db = _Conn([(10, 2, 100)])
    assert _resolve_member_user_id(db, 1, 10) is None


def test_missing_member_returns_none():
    db = _Conn([])
    assert _resolve_member_user_id(db, 1, 123) is None


def test_resolution_uses_multi_index_or_not_a_server_wide_scan():
    # The OR must keep the two point lookups of the original pair of queries.
    # A single `server_id = ? AND (id = ? OR user_id = ?)` decays into a scan
    # over every member of the server on SQLite's planner.
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE srv_members (
            id INTEGER PRIMARY KEY,
            server_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            UNIQUE(server_id, user_id)
        );
        CREATE INDEX idx_srv_members_server ON srv_members(server_id);
        CREATE INDEX idx_srv_members_user ON srv_members(user_id);
        """
    )
    db = _Conn([])
    db._conn = conn
    db._conn.row_factory = sqlite3.Row
    _resolve_member_user_id(db, 1, 10)
    sql, _ = db.calls[0]
    plan = [
        row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, (10, 1, 1, 10, 10))
    ]
    assert any("MULTI-INDEX OR" in step for step in plan), plan
    assert not any("SCAN srv_members" in step for step in plan), plan


def test_all_four_mutation_routes_use_the_single_pass_helper():
    source = inspect.getsource(members)
    for name in (
        "update_server_member",
        "kick_member",
        "assign_role_to_member",
        "remove_role_from_member",
    ):
        endpoint = getattr(members, name)
        body = inspect.getsource(endpoint)
        assert "_resolve_member_user_id(db, sid, mid)" in body, name
        assert "fetch_one" not in body, name
    assert source.count("_resolve_member_user_id(db, sid, mid)") == 4


def test_bulk_lookup_ids_are_deduplicated_in_first_seen_order():
    rows = [
        {"user_id": 30},
        {"user_id": 10},
        {"user_id": 30},  # duplicate membership row
        {"user_id": 20},
        {"user_id": 10},
        {"user_id": 0},  # falsy ids are skipped, as before
    ]
    assert _unique_member_user_ids(rows) == [30, 10, 20]


def test_bulk_lookup_ids_accept_member_objects():
    class Member:
        def __init__(self, user_id):
            self.user_id = user_id

    assert _unique_member_user_ids([Member(7), Member(7), Member(8)]) == [7, 8]


def test_member_list_route_uses_the_dedup_helper():
    source = inspect.getsource(members.get_server_members)
    assert "_unique_member_user_ids(members)" in source
