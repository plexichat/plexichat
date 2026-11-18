"""Query-count regressions for admin moderation routes.

``_ensure_can_target_admin`` guards the platform-wide ban/unban endpoints.
It used to probe ``admin_users`` and then call ``can_manage_admin``, which
fetches the actor's and target's max role positions independently -- three
round trips for a decision made from three scalar values.  The route now
resolves all three in one statement; these tests pin the query count and
verify the batched decision still matches the canonical permission helper.

``get_enhanced_reports`` used to issue a page query and a separate COUNT
query; the COUNT window folds both into one scan.  These tests also cover
that path (including the empty-offset fallback and row-cache safety).
"""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request

import src.api as api
from src.api.routes.admin import moderation as moderation_routes
from src.core.admin import permissions as perm_core


class _CountingDB:
    """Minimal DB double that records every lookup."""

    def __init__(self, row):
        self.row = row
        self.fetch_calls = []

    def fetch_one(self, sql, params=()):
        self.fetch_calls.append((sql, tuple(params)))
        return self.row

    def fetch_all(self, sql, params=()):
        self.fetch_calls.append((sql, tuple(params)))
        return []


def _row(exists=1, current=100, target=50, tuple_row=False):
    if tuple_row:
        return (exists, current, target)
    return {
        "target_exists": exists,
        "current_pos": current,
        "target_pos": target,
    }


@pytest.mark.unit
def test_target_admin_context_is_one_query_and_maps_both_row_shapes():
    for tuple_row in (False, True):
        db = _CountingDB(_row(tuple_row=tuple_row))
        result = moderation_routes._load_target_admin_context(db, 7, 2)

        assert len(db.fetch_calls) == 1
        assert db.fetch_calls[0][1] == (2, 7, 2)
        assert result == (True, 100, 50)


@pytest.mark.unit
def test_target_admin_context_absent_row_defaults_closed():
    db = _CountingDB(None)
    assert moderation_routes._load_target_admin_context(db, 7, 2) == (False, 0, 0)
    assert len(db.fetch_calls) == 1


@pytest.mark.unit
def test_ensure_can_target_admin_is_a_single_lookup_for_admin_target(monkeypatch):
    db = _CountingDB(_row(exists=1, current=100, target=50))
    monkeypatch.setattr(api, "get_db", lambda: db)

    moderation_routes._ensure_can_target_admin(7, 2)

    assert len(db.fetch_calls) == 1


@pytest.mark.unit
def test_ensure_can_target_admin_skips_decision_for_non_admin(monkeypatch):
    db = _CountingDB(_row(exists=0))
    monkeypatch.setattr(api, "get_db", lambda: db)

    moderation_routes._ensure_can_target_admin(7, 2)

    assert len(db.fetch_calls) == 1


@pytest.mark.unit
def test_ensure_can_target_admin_rejects_insufficient_position(monkeypatch):
    from fastapi import HTTPException

    db = _CountingDB(_row(exists=1, current=50, target=100))
    monkeypatch.setattr(api, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        moderation_routes._ensure_can_target_admin(7, 2)

    assert exc.value.status_code == 403
    assert len(db.fetch_calls) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "current_pos,target_pos,actor,target",
    [
        (100, 50, 7, 2),
        (50, 100, 7, 2),
        (50, 50, 7, 2),
        (100, 100, 7, 7),
        (0, 0, 7, 2),
    ],
)
def test_decision_matches_core_helper(
    monkeypatch, current_pos, target_pos, actor, target
):
    monkeypatch.setattr(
        perm_core,
        "get_admin_max_position",
        lambda db, aid: current_pos if aid == actor else target_pos,
    )
    expected = perm_core.can_manage_admin(object(), actor, target)
    assert (
        moderation_routes._can_manage_admin_at(actor, target, current_pos, target_pos)
        is expected
    )


class _ReportDB:
    """DB double for the enhanced-report list, recording every lookup."""

    def __init__(self, rows, count=0):
        self.rows = rows
        self.count = count
        self.fetch_all_calls = []
        self.fetch_one_calls = []

    def fetch_all(self, sql, params=()):
        self.fetch_all_calls.append((sql, tuple(params)))
        return self.rows

    def fetch_one(self, sql, params=()):
        self.fetch_one_calls.append((sql, tuple(params)))
        return {"count": self.count}


def _patch_report_gate(monkeypatch, db):
    monkeypatch.setattr(moderation_routes, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(moderation_routes, "get_admin_from_token", lambda r: 1)
    monkeypatch.setattr(moderation_routes, "require_admin_permission", lambda r, p: 1)
    monkeypatch.setattr(api, "get_db", lambda: db)


def _report_request():
    return cast(Request, SimpleNamespace())


def _report_row(report_id=1, total=5):
    return {
        "id": report_id,
        "status": "open",
        "created_at": 1000 + report_id,
        "reason": "spam",
        "_report_total": total,
    }


@pytest.mark.unit
def test_enhanced_reports_page_and_total_use_one_query(monkeypatch):
    db = _ReportDB([_report_row(2), _report_row(1)])
    _patch_report_gate(monkeypatch, db)

    body = asyncio.run(
        moderation_routes.get_enhanced_reports(
            _report_request(), status_filter=None, limit=50, offset=0
        )
    )

    assert len(db.fetch_all_calls) == 1
    assert db.fetch_one_calls == []
    assert "_report_total" in db.fetch_all_calls[0][0]
    assert db.fetch_all_calls[0][1] == (50, 0)
    assert body["total"] == 5
    assert body["has_more"] is True
    assert all("_report_total" not in item for item in body["items"])


@pytest.mark.unit
def test_enhanced_reports_status_filter_binds_index_column(monkeypatch):
    db = _ReportDB([_report_row()])
    _patch_report_gate(monkeypatch, db)

    body = asyncio.run(
        moderation_routes.get_enhanced_reports(
            _report_request(), status_filter="open", limit=10, offset=5
        )
    )

    assert len(db.fetch_all_calls) == 1
    sql, params = db.fetch_all_calls[0]
    assert "status = ?" in sql
    assert "ORDER BY created_at DESC" in sql
    assert params == ("open", 10, 5)
    assert body["total"] == 5


@pytest.mark.unit
def test_enhanced_reports_offset_past_end_falls_back_to_count(monkeypatch):
    db = _ReportDB([], count=7)
    _patch_report_gate(monkeypatch, db)

    body = asyncio.run(
        moderation_routes.get_enhanced_reports(
            _report_request(), status_filter="open", limit=10, offset=50
        )
    )

    assert len(db.fetch_all_calls) == 1
    assert len(db.fetch_one_calls) == 1
    assert db.fetch_one_calls[0][1] == ("open",)
    assert body == {"items": [], "total": 7, "has_more": False}


@pytest.mark.unit
def test_enhanced_reports_empty_first_page_needs_no_count(monkeypatch):
    db = _ReportDB([], count=7)
    _patch_report_gate(monkeypatch, db)

    body = asyncio.run(
        moderation_routes.get_enhanced_reports(
            _report_request(), status_filter=None, limit=10, offset=0
        )
    )

    assert len(db.fetch_all_calls) == 1
    assert db.fetch_one_calls == []
    assert body == {"items": [], "total": 0, "has_more": False}


@pytest.mark.unit
def test_enhanced_reports_does_not_mutate_cached_rows(monkeypatch):
    row = _report_row()
    db = _ReportDB([row])
    _patch_report_gate(monkeypatch, db)

    for _ in range(2):
        body = asyncio.run(
            moderation_routes.get_enhanced_reports(
                _report_request(), status_filter=None, limit=10, offset=0
            )
        )
        assert body["total"] == 5

    # The DB layer caches these dicts; the route must leave the helper column
    # in place for the next request to read.
    assert row["_report_total"] == 5
