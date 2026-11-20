"""Regression tests for the admin log / artifact read endpoints.

The two oversight reads were slow because every scanned log line was scrubbed
(even though only one page is returned) and because the artifact page query
used a ``COUNT(*) OVER()`` window that sorted the whole table. They were also
under-authorized: log reads accepted any valid admin token without the
``logs.read`` scope, and the filename check did not reject backslashes.

These tests pin the fast, safe behaviour without depending on wall-clock
numbers: they assert *how much work* is done (redaction call counts, query
shape) and that output and security semantics are unchanged.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.routes.admin import artifacts as artifacts_route
from src.api.routes.admin import logs as logs_route
from src.core.admin import logs as admin_logs


@pytest.fixture(autouse=True)
def _clean_log_index():
    admin_logs.clear_log_filter_index()
    yield
    admin_logs.clear_log_filter_index()


def _request(
    query: bytes = b"", path: str = "/api/v1/admin/logs/latest.log"
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": query,
        }
    )


def _seed_log(tmp_path: Path, count: int = 40) -> Path:
    templates = [
        "2026-11-16 12:00:00,000 - INFO - token=supersecret value normal line {i}\n",
        "2026-11-16 12:00:01,000 - ERROR - \x1b[31mrequest failed\x1b[0m {i}\n",
    ]
    text = "".join(templates[i % 2].format(i=i) for i in range(count))
    log = tmp_path / "latest.log"
    log.write_text(text)
    return log


# --------------------------------------------------------------------------- #
# LOGS — perf: redact only what is returned
# --------------------------------------------------------------------------- #


def test_log_redaction_scales_with_page_not_file(tmp_path, monkeypatch):
    _seed_log(tmp_path, count=2000)
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)

    calls = []
    real_redact = admin_logs.redact_secrets

    def counting(value, **kwargs):
        calls.append(value)
        return real_redact(value, **kwargs)

    monkeypatch.setattr(admin_logs, "redact_secrets", counting)

    result = admin_logs.read_log_lines("latest.log", limit=5, offset=0)

    # Two redactions per returned line (message + raw), never one per scanned
    # line. Before the fix this was 4000 calls for a 2000-line file.
    assert result["total_lines"] == 2000
    assert len(result["lines"]) == 5
    assert len(calls) == 10


def test_log_cold_read_is_fast_relative_to_scan(tmp_path, monkeypatch):
    """A cold read must not pay the scrubber's per-line cost for every line."""
    import time

    from src.core.telemetry.scrubbing import redact_secrets, scrub_control_characters

    log = _seed_log(tmp_path, count=4000)
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)

    def _elapsed(fn):
        t0 = time.perf_counter()
        outcome = fn()
        return time.perf_counter() - t0, outcome

    def _old_read():
        # What the previous implementation did: read the whole file *and* scrub
        # every scanned line before slicing out the requested page.
        with open(log, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle.read().splitlines():
                scrub_control_characters(redact_secrets(raw))

    def _cold_read():
        admin_logs.clear_log_filter_index()
        return admin_logs.read_log_lines("latest.log", limit=10, offset=0)

    # Measured in the same process so CPU contention (e.g. parallel CI workers)
    # cancels out. The two sides are interleaved so a burst of load lands on
    # both, and each keeps its fastest run: the comparison is about the work
    # that is skipped, so scheduling noise must not decide it.
    old_cost = float("inf")
    new_cost = float("inf")
    result = None
    for _ in range(5):
        elapsed, _ = _elapsed(_old_read)
        old_cost = min(old_cost, elapsed)
        elapsed, result = _elapsed(_cold_read)
        new_cost = min(new_cost, elapsed)

    assert result["total_lines"] == 4000
    # Only the ten returned lines are scrubbed, not all 4000 scanned lines.
    assert new_cost < old_cost / 3

    # The ratio above is machine-dependent, so pin the invariant itself: the
    # scrubber sees two calls (message + raw) per *returned* line and nothing
    # for the 3990 lines that were only scanned.
    calls = {"redact": 0, "scrub": 0}
    real_redact = admin_logs.redact_secrets
    real_scrub = admin_logs.scrub_control_characters

    def counting_redact(value, **kwargs):
        calls["redact"] += 1
        return real_redact(value, **kwargs)

    def counting_scrub(value, **kwargs):
        calls["scrub"] += 1
        return real_scrub(value, **kwargs)

    monkeypatch.setattr(admin_logs, "redact_secrets", counting_redact)
    monkeypatch.setattr(admin_logs, "scrub_control_characters", counting_scrub)
    admin_logs.clear_log_filter_index()
    again = admin_logs.read_log_lines("latest.log", limit=10, offset=0)

    assert again["lines"] == result["lines"]
    assert calls == {"redact": 2 * 10, "scrub": 2 * 10}


# --------------------------------------------------------------------------- #
# LOGS — identical output + security
# --------------------------------------------------------------------------- #


def test_log_output_matches_reference_scrubbing(tmp_path, monkeypatch):
    _seed_log(tmp_path, count=12)
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)

    result = admin_logs.read_log_lines("latest.log", limit=6, offset=0)

    # Reference: what the previous per-line implementation produced for the
    # same final six lines.
    from src.core.telemetry.scrubbing import redact_secrets, scrub_control_characters

    raw_lines = (tmp_path / "latest.log").read_text().splitlines()
    expected = []
    for raw in raw_lines[-6:]:
        expected.append(
            {
                "timestamp": "2026-11-16 12:00:00,000"
                if "12:00:00" in raw
                else "2026-11-16 12:00:01,000",
                "level": "INFO" if "INFO" in raw else "ERROR",
                "message": scrub_control_characters(
                    redact_secrets(raw.split(" - ", 2)[2])
                ),
                "raw": scrub_control_characters(redact_secrets(raw)),
            }
        )

    assert result["lines"] == expected
    assert result["total_lines"] == 12


def test_log_control_characters_and_secrets_never_leak(tmp_path, monkeypatch):
    _seed_log(tmp_path, count=4)
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)
    result = admin_logs.read_log_lines("latest.log", limit=10, offset=0)

    served = "".join(line["raw"] + line["message"] for line in result["lines"])
    assert "supersecret" not in served
    assert "REDACTED" in served
    assert "\x1b" not in served
    assert "\x00" not in served


def test_log_filter_and_pagination_unchanged(tmp_path, monkeypatch):
    _seed_log(tmp_path, count=12)
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)

    errors = admin_logs.read_log_lines("latest.log", limit=50, level_filter="error")
    assert errors["total_lines"] == 6
    assert all(line["level"] == "ERROR" for line in errors["lines"])

    searched = admin_logs.read_log_lines("latest.log", limit=50, search="failed")
    assert searched["total_lines"] == 6
    assert all("failed" in line["raw"].lower() for line in searched["lines"])

    p1 = admin_logs.read_log_lines("latest.log", limit=2, offset=0)
    p2 = admin_logs.read_log_lines("latest.log", limit=2, offset=2)
    assert p1["lines"][0]["raw"] != p2["lines"][0]["raw"]


@pytest.mark.parametrize(
    "filename",
    [
        "../secret.log",
        "..\\..\\secret.log",
        "sub/app.log",
        "/etc/passwd",
        "app.log\x00.txt",
        "",
    ],
)
def test_log_path_traversal_rejected_at_route(filename, monkeypatch):
    monkeypatch.setattr(logs_route, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(logs_route, "require_admin_permission", lambda r, p: 1)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(logs_route.read_admin_log(_request(), filename=filename))
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "filename", ["../secret.log", "..\\..\\secret.log", "/etc/passwd"]
)
def test_log_path_traversal_rejected_in_core(filename, tmp_path, monkeypatch):
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)
    with pytest.raises(FileNotFoundError):
        admin_logs.read_log_lines(filename)


@pytest.mark.parametrize(
    "handler,args",
    [
        ("list_admin_logs", ()),
        ("read_admin_log", ("latest.log",)),
    ],
)
def test_log_routes_require_logs_read_scope(handler, args, monkeypatch):
    """A valid token with no scope must be rejected (403), not serve logs."""
    import src.api as api_mod
    import src.api.routes.admin.utils as admin_utils
    from src.core.admin import permissions as perms

    monkeypatch.setattr(logs_route, "check_host_restriction", lambda r: None)
    # Authentication succeeds, but the scope check fails.
    monkeypatch.setattr(admin_utils, "get_admin_from_token", lambda r: 1)
    monkeypatch.setattr(perms, "check_admin_permission", lambda *a, **k: False)
    monkeypatch.setattr(api_mod, "get_db", lambda: object())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(getattr(logs_route, handler)(_request(), *args))
    assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# ARTIFACTS — index-friendly query, bounded pagination, scope
# --------------------------------------------------------------------------- #


def test_artifact_query_avoids_full_window_scan():
    class FakeDB:
        def __init__(self):
            self.calls = []

        def fetch_all(self, query, params=None):
            self.calls.append((query, params))
            return [
                {
                    "id": 1,
                    "author_id": 7,
                    "artifact_type": "upload",
                    "title": "t",
                    "status": "completed",
                    "payload": "{}",
                    "created_at": 1,
                    "updated_at": 1,
                    "rev": 0,
                    "_total": 3,
                }
            ]

    db = FakeDB()
    items, total = artifacts_route._list_with_total(db, 10, 0)

    assert len(db.calls) == 1
    assert "COUNT(*) OVER()" not in db.calls[0][0]
    assert "(SELECT COUNT(*) FROM artifacts) AS _total" in db.calls[0][0]
    assert total == 3
    assert [int(a.id) for a in items] == [1]


def test_artifact_pagination_is_bounded(monkeypatch):
    seen = {}

    class FakeDB:
        def fetch_all(self, query, params=None):
            seen["params"] = params
            return []

    manager = MagicMock()
    manager.db = FakeDB()
    monkeypatch.setattr(artifacts_route, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(artifacts_route, "require_admin_permission", lambda r, p: 1)
    monkeypatch.setattr(artifacts_route, "_get_manager", lambda: manager)

    # The maximum allowed page size is forwarded verbatim.
    asyncio.run(
        artifacts_route.admin_list_artifacts(
            _request(b"limit=200&offset=0", "/api/v1/admin/artifacts")
        )
    )
    assert seen["params"] == (200, 0)

    # Anything past the bound is rejected outright rather than clamped to an
    # unbounded scan.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            artifacts_route.admin_list_artifacts(
                _request(b"limit=99999&offset=0", "/api/v1/admin/artifacts")
            )
        )
    assert exc.value.status_code == 400


def test_artifact_route_requires_manage_scope(monkeypatch):
    import src.api as api_mod
    import src.api.routes.admin.utils as admin_utils
    from src.core.admin import permissions as perms

    monkeypatch.setattr(artifacts_route, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(admin_utils, "get_admin_from_token", lambda r: 1)
    monkeypatch.setattr(perms, "check_admin_permission", lambda *a, **k: False)
    monkeypatch.setattr(api_mod, "get_db", lambda: object())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            artifacts_route.admin_list_artifacts(
                _request(path="/api/v1/admin/artifacts")
            )
        )
    assert exc.value.status_code == 403
