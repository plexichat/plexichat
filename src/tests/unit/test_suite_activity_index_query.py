"""The Suite activity feed is one index-backed, actor-scoped query."""

import asyncio
from types import SimpleNamespace

import src.api as api
import src.api.routes.suite.registry as registry


class _ActivityDB:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_all_calls = []
        self.execute_calls = []

    def execute(self, sql, params=None):
        self.execute_calls.append(sql)

    def fetch_all(self, sql, params=()):
        self.fetch_all_calls.append((sql, tuple(params)))
        return self.rows


def _row(rid, domain="stash"):
    return {
        "id": rid,
        "domain": domain,
        "action": "pin",
        "resource_type": "item",
        "resource_id": rid + 100,
        "details_json": '{"pinned": true}',
        "created_at": 1700000000000 + rid,
    }


def _call(db, limit=10, user_id=42):
    monkeypatch_db = api.get_db
    api.get_db = lambda: db
    try:
        return asyncio.run(
            registry.list_suite_activity(
                limit=limit,
                current_user=SimpleNamespace(user_id=user_id),  # pyright: ignore[reportArgumentType]
            )
        )
    finally:
        api.get_db = monkeypatch_db


def test_activity_uses_single_actor_scoped_indexed_query(monkeypatch):
    monkeypatch.setattr(registry, "_activity_index_ready", False)
    db = _ActivityDB([_row(1), _row(2, "sum")])

    result = _call(db, limit=10, user_id=42)

    assert len(db.fetch_all_calls) == 1
    sql, params = db.fetch_all_calls[0]
    assert "WHERE actor_id = ?" in sql
    assert "ORDER BY created_at DESC LIMIT ?" in sql
    # The caller id is bound, never interpolated.
    assert params == (42, 10)
    assert db.execute_calls == [registry._ACTIVITY_INDEX_SQL]
    assert result["total"] == 2
    assert result["items"][0]["appId"] == "plexistash"


def test_activity_index_is_created_once(monkeypatch):
    monkeypatch.setattr(registry, "_activity_index_ready", False)
    db = _ActivityDB([])

    _call(db)
    _call(db)

    assert db.execute_calls == [registry._ACTIVITY_INDEX_SQL]
    assert len(db.fetch_all_calls) == 2
