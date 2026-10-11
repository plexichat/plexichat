"""The self-test summary must surface expected denials, not bury them in "passes".

A licence-gated route answering ``403`` on a free-tier install is recorded with
``success=True`` and ``expected_denial="<feature>:<state>"`` -- an honest pass
(the gate working). Because those rows count as passes, the headline
``Passed: N`` / ``100.0%`` is identical to a run where every check asserted a
2xx. ``report_summary`` therefore also prints an aggregate expected count (and
the per-source breakdown shows the split) so a reader can tell the two apart.

These tests drive the real ``ReportGenerator`` over real result dicts and read
the emitted ``AppLogger`` records via ``caplog`` -- no mocks of the code under
test.
"""

import logging
from types import SimpleNamespace

import pytest


def _ctx(results):
    """Minimal context: just the attributes ``report_summary`` reads."""
    return SimpleNamespace(results=results, start_time=0.0, ram_monitor=None)


def _row(method, path, *, status_code, expected_denial=None):
    return {
        "method": method,
        "path": path,
        "success": True,
        "duration_ms": 1,
        "status_code": status_code,
        "expected_denial": expected_denial,
    }


@pytest.mark.unit
class TestReportExpectedDenials:
    def test_expected_denial_count_is_surfaced(self, caplog):
        from src.core.selftest.services.report import ReportGenerator

        ctx = _ctx(
            [
                _row("GET", "/a", status_code=200),
                _row(
                    "POST",
                    "/api/v1/admin/plexijoin/connections",
                    status_code=403,
                    expected_denial="plexijoin:unlicensed",
                ),
                _row(
                    "GET",
                    "/api/v1/admin/plexijoin/status",
                    status_code=403,
                    expected_denial="plexijoin:unlicensed",
                ),
            ]
        )
        with caplog.at_level(logging.INFO, logger="AppLogger"):
            assert ReportGenerator(ctx).report_summary() is True

        messages = [record.getMessage() for record in caplog.records]

        # The aggregate is named explicitly (two rows carried expected_denial).
        assert any("Expected denials: 2" in m for m in messages), messages

        # They remain passes, so the headline still reports all three as passed.
        assert any(
            m.startswith("Passed:") and m.rstrip().endswith("3") for m in messages
        ), messages

        # The per-source breakdown shows the expected split for the source.
        assert any("expected" in m and "auto_loop" in m for m in messages), messages
        assert any("2 expected" in m for m in messages), messages

    def test_expected_denial_line_absent_when_none(self, caplog):
        from src.core.selftest.services.report import ReportGenerator

        ctx = _ctx([_row("GET", "/a", status_code=200)])
        with caplog.at_level(logging.INFO, logger="AppLogger"):
            assert ReportGenerator(ctx).report_summary() is True

        messages = [record.getMessage() for record in caplog.records]
        assert not any("Expected denials:" in m for m in messages), messages
        # The per-source breakdown omits the expected segment when it is zero.
        assert not any("expected" in m and "total" in m for m in messages), messages
