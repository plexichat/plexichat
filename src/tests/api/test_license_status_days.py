"""License status days_remaining / expiry_date use UTC and round up.

Regression test: a partial day left must report 1 (not 0 via int()
truncation), and the expiry date must be a timezone-aware UTC ISO string
rather than naive local time.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.api.routes.admin.licensing import _days_remaining, _expiry_date_iso


def test_partial_day_rounds_up():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    assert _days_remaining(now + 23 * 3600 + 59 * 60, now_ts=now) == 1
    assert _days_remaining(now + 1, now_ts=now) == 1
    assert _days_remaining(now + 86400, now_ts=now) == 1
    assert _days_remaining(now + 86400 + 1, now_ts=now) == 2


def test_expired_reports_zero():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    assert _days_remaining(now, now_ts=now) == 0
    assert _days_remaining(now - 10, now_ts=now) == 0


def test_expiry_date_is_utc_aware():
    expiry_ts = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    parsed = datetime.fromisoformat(_expiry_date_iso(expiry_ts))
    assert parsed.tzinfo is not None
    assert parsed == datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
