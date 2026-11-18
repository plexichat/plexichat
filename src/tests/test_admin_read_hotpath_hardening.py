"""Hot-path + hardening regressions for the three most-polled admin reads.

Targets:

* ``GET /api/v1/admin/dashboard``
* ``GET /api/v1/admin/reports``
* ``GET /api/v1/admin/dashboard/system-alerts``

The handlers used to recompute a dozen-plus COUNT/full-scan queries on every
poll and only authenticated the caller (no permission scope).  They now gate
on an explicit scope *before* consulting a short-TTL, DB-scoped summary cache,
bound their aggregates, and strip control characters from the free-text labels
they echo back.

These tests pin all three properties: identical output across a cache hit, a
deterministic query-count/warm-path perf signal, and the security gates.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from fastapi import Request

import src.api as api
from src.api.routes.admin import _summary_cache, dashboard, moderation


@pytest.fixture(autouse=True)
def _fresh_summary_cache():
    _summary_cache.clear()
    yield
    _summary_cache.clear()


def _req() -> Request:
    return cast(Request, SimpleNamespace())


class _CountingDB:
    """DB double that records every lookup and returns configured rows."""

    def __init__(self, rows=None, one=None):
        self.rows = rows or []
        self.one = one
        self.fetch_all_calls: list[tuple[str, tuple]] = []
        self.fetch_one_calls: list[tuple[str, tuple]] = []

    def fetch_all(self, sql, params=()):
        self.fetch_all_calls.append((sql, tuple(params)))
        return self.rows

    def fetch_one(self, sql, params=()):
        self.fetch_one_calls.append((sql, tuple(params)))
        return self.one


# ---------------------------------------------------------------------------
# 1. Identical output + warm-path perf
# ---------------------------------------------------------------------------


def test_dashboard_warm_hit_is_identical_and_skips_every_producer(monkeypatch):
    """A second dashboard poll returns the same response without recomputing."""
    calls = {"ticket": 0, "counts": 0, "features": 0, "telemetry": 0}

    def _tickets():
        calls["ticket"] += 1
        return {"open": 1, "in_progress": 0, "resolved": 2, "closed": 0, "total": 3}

    def _counts():
        calls["counts"] += 1
        return {
            "total_users": 10,
            "active_users": 4,
            "scheduled_deletions": 1,
            "db_status": "healthy",
        }

    def _features():
        calls["features"] += 1
        return {
            "bookmarks": 3,
            "report_categories": [{"category": "spam\r\ninjected", "count": 2}],
        }

    def _telemetry_setup():
        calls["telemetry"] += 1
        return False

    monkeypatch.setattr(dashboard, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(dashboard, "require_admin_permission", lambda r, p: 1)
    with (
        patch("src.core.admin.get_ticket_counts", side_effect=_tickets),
        patch("src.core.telemetry.is_setup", side_effect=_telemetry_setup),
        patch.object(dashboard.applications, "get_admin_dashboard_counts", side_effect=_counts),
        patch.object(
            dashboard.applications,
            "get_admin_dashboard_feature_stats",
            side_effect=_features,
        ),
    ):
        first = asyncio.run(dashboard.get_dashboard(_req()))
        second = asyncio.run(dashboard.get_dashboard(_req()))

    assert first == second
    # Every expensive producer ran exactly once — the second poll was served
    # from the short-TTL cache without any DB-backed helper work.
    assert calls == {"ticket": 1, "counts": 1, "features": 1, "telemetry": 1}
    # The free-text category label is sanitized on the way out.
    assert first.feature_stats["report_categories"][0]["category"] == "spaminjected"


def test_system_alerts_warm_hit_is_identical_and_uses_one_query(monkeypatch):
    db = _CountingDB(
        rows=[
            {
                "id": 1,
                "source": "valkey\nalert",
                "event_type": "down",
                "severity": "warn\x1fing",
                "details": "d",
                "target_path": "/p",
                "created_at": 100,
            }
        ]
    )
    monkeypatch.setattr(dashboard, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(dashboard, "require_admin_permission", lambda r, p: 1)
    with patch.object(api, "get_db", return_value=db):
        first = asyncio.run(
            dashboard.get_system_alerts(
                _req(), limit=50, hours=24, source=None, severity=None
            )
        )
        second = asyncio.run(
            dashboard.get_system_alerts(
                _req(), limit=50, hours=24, source=None, severity=None
            )
        )

    assert first == second
    assert len(db.fetch_all_calls) == 1
    # Control chars are stripped from the label fields only.
    assert first["entries"][0]["source"] == "valkeyalert"
    assert first["entries"][0]["severity"] == "warning"
    assert first["entries"][0]["details"] == "d"


def test_reports_warm_hit_is_identical_and_uses_one_query(monkeypatch):
    db = _CountingDB(
        rows=[
            {
                "id": 1,
                "status": "open\n",
                "category": "spam\rX",
                "reason": "body text",
                "_report_total": 1,
            }
        ]
    )
    monkeypatch.setattr(moderation, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(moderation, "require_admin_permission", lambda r, p: 1)
    with patch.object(api, "get_db", return_value=db):
        first = asyncio.run(
            moderation.get_enhanced_reports(
                _req(), status_filter=None, limit=50, offset=0
            )
        )
        second = asyncio.run(
            moderation.get_enhanced_reports(
                _req(), status_filter=None, limit=50, offset=0
            )
        )

    assert first == second
    assert len(db.fetch_all_calls) == 1
    assert first["items"][0]["status"] == "open"
    assert first["items"][0]["category"] == "spamX"
    # The report body is not a label and is left untouched.
    assert first["items"][0]["reason"] == "body text"
    assert "_report_total" not in first["items"][0]


def test_cache_is_scoped_to_the_backing_database(monkeypatch):
    """A cached summary from one DB is never served for a different DB."""
    db1 = _CountingDB(rows=[{"id": 1, "source": "one", "event_type": "e", "severity": "info", "details": None, "target_path": None, "created_at": 1}])
    db2 = _CountingDB(rows=[{"id": 2, "source": "two", "event_type": "e", "severity": "info", "details": None, "target_path": None, "created_at": 2}])
    monkeypatch.setattr(dashboard, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(dashboard, "require_admin_permission", lambda r, p: 1)

    with patch.object(api, "get_db", return_value=db1):
        first = asyncio.run(
            dashboard.get_system_alerts(
                _req(), limit=50, hours=24, source=None, severity=None
            )
        )
    with patch.object(api, "get_db", return_value=db2):
        second = asyncio.run(
            dashboard.get_system_alerts(
                _req(), limit=50, hours=24, source=None, severity=None
            )
        )

    assert first["entries"][0]["source"] == "one"
    assert second["entries"][0]["source"] == "two"
    assert first != second
    assert len(db1.fetch_all_calls) == 1
    assert len(db2.fetch_all_calls) == 1


# ---------------------------------------------------------------------------
# 2. Security: missing scope on reads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler,scope",
    [
        (dashboard.get_system_alerts, "metrics.read"),
        (dashboard.get_system_alert_stats, "metrics.read"),
        (moderation.get_enhanced_reports, "reports.read"),
        (moderation.get_enhanced_report_counts, "reports.read"),
    ],
)
def test_read_handlers_require_an_explicit_scope(handler, scope):
    source = inspect.getsource(handler)
    assert "require_admin_permission(" in source, handler.__name__
    assert f'"{scope}"' in source, (handler.__name__, scope)
    # Authentication-only reads are no longer present on these handlers.
    assert "get_admin_from_token(" not in source, handler.__name__


def test_scope_gate_is_enforced_even_on_a_cache_hit(monkeypatch):
    from fastapi import HTTPException

    db = _CountingDB(rows=[])
    monkeypatch.setattr(dashboard, "check_host_restriction", lambda r: None)

    def _deny(request, permission):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    monkeypatch.setattr(dashboard, "require_admin_permission", _deny)
    with patch.object(api, "get_db", return_value=db):
        # Prime the cache with an authorized read.
        monkeypatch.setattr(dashboard, "require_admin_permission", lambda r, p: 1)
        asyncio.run(
            dashboard.get_system_alerts(
                _req(), limit=50, hours=24, source=None, severity=None
            )
        )
        # Now deny: the cached value must NOT bypass the permission gate.
        monkeypatch.setattr(dashboard, "require_admin_permission", _deny)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                dashboard.get_system_alerts(
                    _req(), limit=50, hours=24, source=None, severity=None
                )
            )
    assert exc.value.status_code == 403
    assert len(db.fetch_all_calls) == 1  # no extra query, but still denied


# ---------------------------------------------------------------------------
# 3. Security: bounded aggregates + control-char labels
# ---------------------------------------------------------------------------


def test_system_alert_stats_aggregate_is_bounded(monkeypatch):
    db = _CountingDB(
        rows=[{"source": "a\nb", "severity": "c\td", "cnt": 5}]
    )
    monkeypatch.setattr(dashboard, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(dashboard, "require_admin_permission", lambda r, p: 1)
    with patch.object(api, "get_db", return_value=db):
        result = asyncio.run(dashboard.get_system_alert_stats(_req(), hours=24))

    sql = db.fetch_all_calls[0][0]
    assert "GROUP BY source, severity" in sql
    assert "LIMIT 200" in sql  # bounded number of label buckets
    assert result["total"] == 5
    assert result["by_source"] == {"ab": {"cd": 5}}


def test_report_counts_aggregate_is_bounded(monkeypatch):
    db = _CountingDB(rows=[{"status": "open\rx", "count": 3}])
    monkeypatch.setattr(moderation, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(moderation, "require_admin_permission", lambda r, p: 1)
    with patch.object(api, "get_db", return_value=db):
        result = asyncio.run(moderation.get_enhanced_report_counts(_req()))

    sql = db.fetch_all_calls[0][0]
    assert "GROUP BY status" in sql
    assert "LIMIT 200" in sql
    assert result == {"openx": 3, "total": 3}


def test_labels_with_line_separators_are_sanitized():
    # U+2028/U+2029 are line separators for many log shippers and JS stacks.
    assert dashboard._safe_label("ok\u2028bad\u2029") == "okbad"
    assert moderation._safe_label(None) is None
