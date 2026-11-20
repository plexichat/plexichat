"""Regression tests for the admin audit stats day boundary.

``admin_audit_log.created_at`` is stored as UTC millis (``int(time.time() *
1000)``), so the "today" boundary used by ``GET /audit/logs/stats`` must be
UTC midnight. A naive server-local ``datetime.now()`` midnight is off by the
host UTC offset on any non-UTC host.
"""

import os
import time
from datetime import timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def non_utc_host(monkeypatch):
    """Run the test as if the server lived in a non-UTC timezone."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    # monkeypatch restores TZ afterwards; re-sync the C library clock.
    time.tzset()


class _CapturingDB:
    """Fake DB that records the params passed to each count query."""

    def __init__(self):
        self.seen_params = []

    def fetch_one(self, query, params=None):
        self.seen_params.append(tuple(params or ()))
        if "total_today" in query:
            from src.api.routes.admin import audit as audit_routes

            return {name: 0 for name in audit_routes._AUDIT_STATS_COLUMNS}
        return {"count": 0}


def _stats_client(fake_db):
    from src.api.routes.admin import audit as audit_routes

    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    return client, ctx, audit_routes


def test_stats_today_boundary_is_utc_midnight(non_utc_host):
    """today_start must equal UTC midnight even on a non-UTC host."""
    from datetime import datetime as real_datetime

    fake_db = _CapturingDB()
    client, ctx, audit_routes = _stats_client(fake_db)
    try:
        # Noon UTC on 2026-06-15 (08:00 EDT in America/New_York).
        fixed_now = real_datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        class _FixedDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        with patch.object(audit_routes, "datetime", _FixedDateTime):
            response = client.get("/api/v1/admin/audit/logs/stats")
        assert response.status_code == 200

        expected = int(
            real_datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
        )
        assert fake_db.seen_params, "expected the stats route to query the DB"
        for params in fake_db.seen_params:
            assert params[0] == expected
    finally:
        for c in reversed(ctx):
            c.stop()


def test_stats_counts_bucketed_by_action_family():
    """Sanity check: per-family counts only include families with rows."""
    fake_db = _CapturingDB()
    client, ctx, _ = _stats_client(fake_db)
    try:
        response = client.get("/api/v1/admin/audit/logs/stats")
        assert response.status_code == 200
        body = response.json()
        assert body["total_today"] == 0
        assert body["success_today"] == 0
        assert body["failed_today"] == 0
        assert body["by_action_family"] == {}
        # One conditional-aggregate query replaces the old 11 COUNT scans.
        assert len(fake_db.seen_params) == 1
    finally:
        for c in reversed(ctx):
            c.stop()


def test_tz_fixture_restores_utc():
    """Guard: the non-UTC fixture above must not leak TZ into other tests."""
    assert os.environ.get("TZ") != "America/New_York"


class _ExportDB:
    """Fake DB returning a fixed audit row for the CSV export."""

    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self, query, params=None):
        return self._rows


def test_export_csv_timestamp_is_utc(non_utc_host):
    """The CSV Timestamp column must be UTC-aware, not naive server-local."""
    from datetime import datetime

    from src.api.routes.admin import audit as audit_routes

    epoch_ms = int(datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    row = {
        "id": 1,
        "admin_id": 2,
        "action": "role.grant",
        "target_type": "admin_user",
        "target_id": "3",
        "details": "",
        "ip_address": "1.2.3.4",
        "user_agent": "ua",
        "status": "success",
        "created_at": epoch_ms,
    }
    fake_db = _ExportDB([row])

    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    try:
        response = client.get("/api/v1/admin/audit/logs/export?format=csv")
        assert response.status_code == 200
        # UTC noon; on the America/New_York host this is 08:00 local.
        assert "2026-06-15T12:00:00+00:00" in response.text
    finally:
        for c in reversed(ctx):
            c.stop()


class _ListDB:
    """Fake DB for the paginated list: a count plus fixed rows."""

    def __init__(self, rows):
        self._rows = rows

    def fetch_one(self, query, params=None):
        return {"total": len(self._rows)}

    def fetch_all(self, query, params=None):
        return self._rows


def test_list_timestamp_is_utc(non_utc_host):
    """GET /audit/logs must render ``timestamp`` as UTC, like the export."""
    from datetime import datetime

    from src.api.routes.admin import audit as audit_routes

    epoch_ms = int(datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    row = {
        "id": 1,
        "admin_id": 2,
        "action": "role.grant",
        "target_type": "admin_user",
        "target_id": "3",
        "details": "",
        "ip_address": "1.2.3.4",
        "user_agent": "ua",
        "status": "success",
        "created_at": epoch_ms,
    }
    fake_db = _ListDB([row])

    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    try:
        response = client.get("/api/v1/admin/audit/logs")
        assert response.status_code == 200
        # UTC noon; a naive server-local render on the non-UTC host would be 08:00.
        assert response.json()["logs"][0]["timestamp"] == "2026-06-15T12:00:00+00:00"
    finally:
        for c in reversed(ctx):
            c.stop()


class _RecordingDB:
    """Fake DB that records the SQL and params passed to each query."""

    def __init__(self):
        self.calls = []

    def fetch_one(self, query, params=None):
        self.calls.append((query, tuple(params or ())))
        return {"total": 0}

    def fetch_all(self, query, params=None):
        return []


def test_list_date_from_zero_is_applied():
    """``date_from=0`` is a real epoch bound and must not be dropped as falsy."""
    from src.api.routes.admin import audit as audit_routes

    fake_db = _RecordingDB()
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    try:
        response = client.get("/api/v1/admin/audit/logs?date_from=0")
        assert response.status_code == 200
        count_query, count_params = fake_db.calls[0]
        assert "created_at >= ?" in count_query
        assert count_params == (0,)
    finally:
        for c in reversed(ctx):
            c.stop()


def test_list_stringifies_target_id_snowflake():
    """A user/server ``target_id`` must cross the wire as a decimal string.

    ``admin_audit_log.target_id`` holds the target entity's snowflake when the
    target is a user/server/channel. Served as a JSON number a JS client rounds
    it above 2^53, so the admin list must stringify it (like the self
    audit-log endpoint). The acting ``admin_id`` is a snowflake too; only the
    audit row's own rowid stays numeric.
    """
    from src.api.routes.admin import audit as audit_routes

    big = 9007199254740993  # past 2^53: a JSON number would lose precision
    row = {
        "id": 1,
        "admin_id": 2,
        "action": "user.ban",
        "target_type": "user",
        "target_id": big,
        "details": "",
        "ip_address": "1.2.3.4",
        "user_agent": "ua",
        "status": "success",
        "created_at": 1700000000000,
    }
    fake_db = _ListDB([row])

    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    try:
        response = client.get("/api/v1/admin/audit/logs")
        assert response.status_code == 200
        entry = response.json()["logs"][0]
        assert entry["target_id"] == str(big)
        assert isinstance(entry["target_id"], str)
        # The acting admin's id is a snowflake too and must be a string.
        assert entry["admin_id"] == "2"
        assert isinstance(entry["admin_id"], str)
        # The audit row's own rowid stays numeric.
        assert entry["id"] == 1
    finally:
        for c in reversed(ctx):
            c.stop()


class _ExportRecordingDB:
    """Fake DB that records the SQL/params passed to the export query."""

    def __init__(self):
        self.calls = []

    def fetch_one(self, query, params=None):
        return {"total": 0}

    def fetch_all(self, query, params=None):
        self.calls.append((query, tuple(params or ())))
        return []


def test_list_escapes_action_family_like_wildcards():
    """A ``%``/``_`` in ``action_family`` must be matched literally.

    Parameterization blocks injection but not wildcards, so a bare ``%`` would
    otherwise dump every action (docs/admin-hardening.md).
    """
    from src.api.routes.admin import audit as audit_routes

    fake_db = _RecordingDB()
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    try:
        # URL-encoded ``100%_off``.
        response = client.get("/api/v1/admin/audit/logs?action_family=100%25_off")
        assert response.status_code == 200
        count_query, count_params = fake_db.calls[0]
        assert "action LIKE ? ESCAPE '\\'" in count_query
        assert count_params == ("100\\%\\_off%",)
    finally:
        for c in reversed(ctx):
            c.stop()


def test_export_escapes_action_family_like_wildcards():
    """The CSV/JSON export must escape ``action_family`` like the list endpoint."""
    from src.api.routes.admin import audit as audit_routes

    fake_db = _ExportRecordingDB()
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    try:
        response = client.get(
            "/api/v1/admin/audit/logs/export?format=csv&action_family=%25"
        )
        assert response.status_code == 200
        query, params = fake_db.calls[0]
        assert "action LIKE ? ESCAPE '\\'" in query
        # Export reads are bounded to a single page per query; later pages are
        # keyset-fetched lazily while the overall cap still applies.
        assert params == ("\\%%", audit_routes._AUDIT_EXPORT_CHUNK_SIZE)
        assert audit_routes._AUDIT_EXPORT_CHUNK_SIZE <= 10_000
    finally:
        for c in reversed(ctx):
            c.stop()


# --- Batch the cold audit-stats build into a single scoped query -------------


class _StatsRecordingDB:
    """Fake DB returning one conditional-aggregate row, recording the call."""

    def __init__(self, row=None):
        self.calls = []
        self.row = row

    def fetch_one(self, query, params=None):
        self.calls.append((query, tuple(params or ())))
        return self.row


def _stats_result_row(**overrides):
    from src.api.routes.admin import audit as audit_routes

    values = {name: 0 for name in audit_routes._AUDIT_STATS_COLUMNS}
    values.update(overrides)
    return values


def test_audit_stats_cold_build_is_one_query_not_eleven():
    """A cold build reads the day's rows once, not in 11 separate scans.

    Before the change the producer issued three COUNT scans for the totals plus
    eight ``LIKE 'family%'`` COUNT scans, each a separate pass over the same
    ``created_at >= day_start`` slice. A single conditional-aggregate query must
    return an identical payload with one round-trip.
    """
    from src.api.routes.admin import audit as audit_routes

    today = 1_750_000_000_000
    row = _stats_result_row(
        total_today=100,
        success_today=70,
        failed_today=20,
        fam_login=40,
        fam_user=30,
        # other families stay at zero and must be omitted from the payload
    )
    db = _StatsRecordingDB(row)
    with patch("src.api.get_db", return_value=db):
        stats = audit_routes._build_audit_stats(today)

    assert len(db.calls) == 1, "cold build must issue exactly one query"
    sql, params = db.calls[0]
    # One scoped read, conditional aggregates, bound family patterns.
    assert "created_at >= ?" in sql
    assert "COUNT(*) AS total_today" in sql
    assert sql.count("CASE WHEN") == len(audit_routes._AUDIT_STATS_FAMILIES) + 2
    # today_start stays the first bound parameter; families are parameters too,
    # never interpolated into the SQL text.
    assert params[0] == today
    assert params[1:] == tuple(
        f"{family}%" for family in audit_routes._AUDIT_STATS_FAMILIES
    )
    for family in audit_routes._AUDIT_STATS_FAMILIES:
        assert f"'{family}%'" not in sql

    assert stats == {
        "total_today": 100,
        "success_today": 70,
        "failed_today": 20,
        "by_action_family": {"login": 40, "user": 30},
    }


def test_audit_stats_accepts_positional_rows_and_fails_closed():
    """Tuple adapters map by column order; a missing row is not a zero summary."""
    from src.api.routes.admin import audit as audit_routes

    today = 1_750_000_000_000
    ordered = [0] * len(audit_routes._AUDIT_STATS_COLUMNS)
    ordered[audit_routes._AUDIT_STATS_COLUMNS.index("total_today")] = 7
    ordered[audit_routes._AUDIT_STATS_COLUMNS.index("fam_security")] = 3

    with patch("src.api.get_db", return_value=_StatsRecordingDB(tuple(ordered))):
        stats = audit_routes._build_audit_stats(today)
    assert stats == {
        "total_today": 7,
        "success_today": 0,
        "failed_today": 0,
        "by_action_family": {"security": 3},
    }

    # A missing row means the store gave us no usable answer: fail closed rather
    # than serving a fabricated "all zeros" summary to the admin dashboard.
    from fastapi import HTTPException

    with patch("src.api.get_db", return_value=_StatsRecordingDB(None)):
        with pytest.raises(HTTPException) as exc:
            audit_routes._build_audit_stats(today)
    assert exc.value.status_code == 500


def test_audit_stats_batched_payload_matches_old_scans():
    """Real SQLite: one aggregate query returns exactly what 11 scans did."""
    import sqlite3

    from src.api.routes.admin import audit as audit_routes

    db = sqlite3.connect(":memory:")
    db.row_factory = lambda cur, row: {
        d[0]: row[i] for i, d in enumerate(cur.description)
    }
    db.execute(
        "CREATE TABLE admin_audit_log (id INTEGER PRIMARY KEY, created_at INTEGER, "
        "status TEXT, action TEXT)"
    )
    families = audit_routes._AUDIT_STATS_FAMILIES
    rows = [
        (
            i,
            1000 + (i % 100),
            ("success", "failed", "warn")[i % 3],
            f"{families[i % len(families)]}.evt.{i}",
        )
        for i in range(5000)
    ]
    db.executemany("INSERT INTO admin_audit_log VALUES (?,?,?,?)", rows)
    db.commit()

    day = 0
    expected = {
        "total_today": db.execute(
            "SELECT COUNT(*) AS c FROM admin_audit_log WHERE created_at >= ?", (day,)
        ).fetchone()["c"],
        "success_today": db.execute(
            "SELECT COUNT(*) AS c FROM admin_audit_log WHERE created_at >= ? "
            "AND status = 'success'",
            (day,),
        ).fetchone()["c"],
        "failed_today": db.execute(
            "SELECT COUNT(*) AS c FROM admin_audit_log WHERE created_at >= ? "
            "AND status = 'failed'",
            (day,),
        ).fetchone()["c"],
        "by_action_family": {},
    }
    for fam in families:
        count = db.execute(
            "SELECT COUNT(*) AS c FROM admin_audit_log WHERE created_at >= ? "
            "AND action LIKE ?",
            (day, f"{fam}%"),
        ).fetchone()["c"]
        if count > 0:
            expected["by_action_family"][fam] = count

    class _DB:
        def fetch_one(self, query, params=None):
            return db.execute(query, params or ()).fetchone()

    with patch("src.api.get_db", return_value=_DB()):
        actual = audit_routes._build_audit_stats(day)
    assert actual == expected
