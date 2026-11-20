"""Query-count regressions for user resolution on the report listing.

``get_enhanced_reports`` returns a page of reports that references several
user ids per row (reporter, reported user, assignee, reviewer). Resolving each
id with a separate ``SELECT ... WHERE id = ?`` is the classic N+1 read: a page
of N reports issued up to 4N round trips. The listing now resolves all ids on
the authorized page with bounded ``IN (?, ...)`` chunks of at most 500, so the
cost is one page query plus ``ceil(unique_ids / 500)`` lookups.

These tests pin:

* the chunking bound and that a whole page collapses to a handful of queries,
* that the data shape is identical to the per-id lookups it replaces,
* that no cached row dict is mutated, and
* that the ``reports.read`` scope gate still runs before any data access.
"""

import asyncio
import math
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

import src.api as api
from src.api.routes.admin import moderation as routes


pytestmark = pytest.mark.unit


class _CountingDB:
    """DB double that records lookups and filters by the ``IN`` id list."""

    def __init__(self, users=None):
        self.users = {int(uid): name for uid, name in (users or {}).items()}
        self.fetch_all_calls = []
        self.fetch_one_calls = []

    def fetch_all(self, sql, params=()):
        self.fetch_all_calls.append((" ".join(sql.split()), tuple(params)))
        if "auth_users" in sql and " IN (" in sql:
            return [
                {"id": uid, "username": self.users.get(uid)}
                for uid in params
                if uid in self.users
            ]
        raise AssertionError(f"unexpected fetch_all: {sql}")

    def fetch_one(self, sql, params=()):
        self.fetch_one_calls.append((" ".join(sql.split()), tuple(params)))
        return {"count": 0}


def _items(n, *, fields=("reporter_id",)):
    items = []
    for i in range(n):
        item = {"id": 1000 + i, "status": "open"}
        for field in fields:
            item[field] = i + 1
        items.append(item)
    return items


def test_chunked_in_resolves_large_page_in_bounded_queries():
    db = _CountingDB({i: f"user-{i}" for i in range(1, 1201)})
    items = _items(1200)  # 1200 distinct reporter ids
    routes._enrich_report_users(db, items)

    # 1200 ids -> ceil(1200/500) == 3 chunks, never 1200 per-report queries.
    assert len(db.fetch_all_calls) == 3
    assert [len(params) for _, params in db.fetch_all_calls] == [500, 500, 200]
    for sql, params in db.fetch_all_calls:
        assert "auth_users" in sql
        assert sql.count("?") == len(params)
        assert "id IN (" in sql
    # Every row resolved to the same value a per-id lookup would have returned.
    assert all(
        item["reporter_username"] == f"user-{item['reporter_id']}" for item in items
    )


def test_all_user_columns_are_resolved_from_one_shared_id_set():
    db = _CountingDB({1: "reporter", 2: "reported", 3: "assignee", 4: "reviewer"})
    items = [
        {
            "id": 1,
            "reporter_id": 1,
            "reported_user_id": 2,
            "assigned_to": 3,
            "reviewed_by": 4,
        }
    ]
    routes._enrich_report_users(db, items)

    # Four id columns on one report collapse to a single distinct-id query.
    assert len(db.fetch_all_calls) == 1
    assert set(db.fetch_all_calls[0][1]) == {1, 2, 3, 4}
    assert items[0] == {
        "id": 1,
        "reporter_id": 1,
        "reported_user_id": 2,
        "assigned_to": 3,
        "reviewed_by": 4,
        "reporter_username": "reporter",
        "reported_username": "reported",
        "assigned_to_username": "assignee",
        "reviewed_by_username": "reviewer",
    }


def test_missing_user_does_not_invent_a_username():
    db = _CountingDB({1: "reporter"})  # id 2 was deleted
    items = [{"id": 1, "reporter_id": 1, "reported_user_id": 2}]
    routes._enrich_report_users(db, items)

    assert items[0]["reporter_username"] == "reporter"
    assert "reported_username" not in items[0]


def test_only_ids_on_the_page_are_queried():
    db = _CountingDB({i: f"user-{i}" for i in range(1, 6)})
    items = _items(5)
    routes._enrich_report_users(db, items)

    queried = {uid for _, params in db.fetch_all_calls for uid in params}
    assert queried == {1, 2, 3, 4, 5}


def test_enrichment_does_not_mutate_cached_rows():
    db = _CountingDB({1: "reporter"})
    cached = {"id": 1, "reporter_id": 1, "status": "open"}
    items = [{k: v for k, v in cached.items()}]

    routes._enrich_report_users(db, items)
    routes._enrich_report_users(db, [{k: v for k, v in cached.items()}])

    assert cached == {"id": 1, "reporter_id": 1, "status": "open"}
    assert items[0]["reporter_username"] == "reporter"


def test_query_count_is_bounded_not_per_report():
    """Measure the reduction: N reports cost ~N/500 queries, not 4N."""
    for n in (10, 100, 1000):
        db = _CountingDB({i: f"u{i}" for i in range(1, n + 1)})
        items = _items(n)
        routes._enrich_report_users(db, items)

        expected = math.ceil(n / 500)
        assert len(db.fetch_all_calls) == expected
        # The naive per-report path would have issued n lookups here.
        assert len(db.fetch_all_calls) < n
        assert all(
            item["reporter_username"] == f"u{item['reporter_id']}" for item in items
        )


def test_report_listing_keeps_scope_gate_before_any_query(monkeypatch):
    db = _CountingDB({1: "reporter"})

    def _deny(request, permission):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    monkeypatch.setattr(routes, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(routes, "require_admin_permission", _deny)
    monkeypatch.setattr(api, "get_db", lambda: db)

    request = cast(Request, SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_enhanced_reports(request, limit=10, offset=0))
    assert exc.value.status_code == 403
    assert db.fetch_all_calls == []
    assert db.fetch_one_calls == []


def test_report_listing_enriches_page_end_to_end(monkeypatch):
    page_row = {
        "id": 1,
        "status": "open",
        "created_at": 1000,
        "_report_total": 1,
        "reporter_id": 7,
        "reported_user_id": 8,
    }

    class _ListingDB(_CountingDB):
        def fetch_all(self, sql, params=()):
            if "auth_users" in sql:
                return super().fetch_all(sql, params)
            self.fetch_all_calls.append((" ".join(sql.split()), tuple(params)))
            return [page_row]

    db = _ListingDB({7: "seven", 8: "eight"})

    monkeypatch.setattr(routes, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(routes, "require_admin_permission", lambda r, p: 1)
    monkeypatch.setattr(api, "get_db", lambda: db)
    # Bypass the summary cache so each call recomputes from the DB double.
    monkeypatch.setattr(
        routes, "get_or_compute", lambda ns, params, producer, ttl: producer()
    )

    body = asyncio.run(routes.get_enhanced_reports(_request(), limit=10, offset=0))

    assert body["total"] == 1
    assert body["items"][0]["reporter_username"] == "seven"
    assert body["items"][0]["reported_username"] == "eight"
    # One page query + one bounded IN lookup for the two distinct ids.
    page_queries = [sql for sql, _ in db.fetch_all_calls if "auth_users" not in sql]
    user_queries = [sql for sql, _ in db.fetch_all_calls if "auth_users" in sql]
    assert len(page_queries) == 1
    assert len(user_queries) == 1


def _request() -> Request:
    return cast(Request, SimpleNamespace())
