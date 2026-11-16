"""Regression tests for telemetry payload, retention and query boundaries."""

import pytest

import src.core.telemetry as telemetry
from src.core.security.hardening import HardeningViolation


class _DB:
    def __init__(self):
        self.rows = []
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def execute_many(self, sql, rows):
        self.rows.extend(rows)

    def fetch_one(self, sql, params=()):
        return {"c": 0}

    def fetch_all(self, sql, params=()):
        return []


@pytest.fixture
def telemetry_db(monkeypatch):
    db = _DB()
    monkeypatch.setattr(telemetry, "_db", db)
    monkeypatch.setattr(telemetry, "_setup_complete", True)
    return db


def _entry(**overrides):
    entry = {
        "endpoint": "/api/v1/messages",
        "method": "GET",
        "response_time_ms": 12.5,
        "status_code": 200,
    }
    entry.update(overrides)
    return entry


class TestEventPayloadCaps:
    def test_batch_entry_cap(self, telemetry_db):
        entries = [_entry() for _ in range(telemetry.MAX_BATCH_ENTRIES + 1)]
        with pytest.raises(HardeningViolation):
            telemetry.submit_response_times(entries)

    def test_total_payload_cap(self, telemetry_db):
        huge = _entry(endpoint="x" * (telemetry.MAX_TOTAL_PAYLOAD_CHARS + 1))
        with pytest.raises(HardeningViolation):
            telemetry.submit_response_times([huge])

    @pytest.mark.parametrize("method", ["GETTERTHATISLONG", "GET\nX-INJECTED"])
    def test_bad_method_drops_entry(self, telemetry_db, method):
        assert telemetry.submit_response_times([_entry(method=method)]) == 0

    def test_control_characters_in_endpoint_drops_entry(self, telemetry_db):
        assert telemetry.submit_response_times([_entry(endpoint="/a\x00b")]) == 0

    def test_oversized_numeric_field_drops_entry(self, telemetry_db):
        entry = _entry(db_time_ms=telemetry.MAX_DB_TIME_MS + 1)
        assert telemetry.submit_response_times([entry]) == 0

    def test_valid_entry_accepted(self, telemetry_db):
        assert telemetry.submit_response_times([_entry()]) == 1


class TestConfigRetentionBounds:
    def test_bounded_cfg_rejects_overflow(self, monkeypatch):
        monkeypatch.setattr(telemetry, "_cfg", lambda key, default: 10**30)
        with pytest.raises(HardeningViolation):
            telemetry._bounded_cfg(
                "telemetry.max_total_rows", 100000, 0, telemetry.MAX_TOTAL_ROWS
            )


class TestQueryWindowBounds:
    @pytest.mark.parametrize("hours", [0, -1, telemetry.MAX_QUERY_HOURS + 1])
    def test_hours_rejected(self, hours):
        with pytest.raises(HardeningViolation):
            telemetry._bounded_window(hours)

    def test_hours_accepted(self):
        assert telemetry._bounded_window(24) == 24
        assert (
            telemetry._bounded_window(telemetry.MAX_QUERY_HOURS)
            == telemetry.MAX_QUERY_HOURS
        )

    def test_bucket_minutes_bounded(self, telemetry_db):
        with pytest.raises(HardeningViolation):
            telemetry.get_response_time_history("/x", bucket_minutes=0)
        with pytest.raises(HardeningViolation):
            telemetry.get_response_time_history(
                "/x", bucket_minutes=telemetry.MAX_BUCKET_MINUTES + 1
            )

    def test_cleanup_days_bounded(self, telemetry_db):
        with pytest.raises(HardeningViolation):
            telemetry.cleanup_old_data(days=-1)

    def test_filter_length_bounded(self):
        with pytest.raises(HardeningViolation):
            telemetry._bounded_filter(
                "x" * (telemetry.MAX_ENDPOINT_FILTER_CHARS + 1),
                "endpoint_filter",
                telemetry.MAX_ENDPOINT_FILTER_CHARS,
            )
        with pytest.raises(HardeningViolation):
            telemetry._bounded_filter("a\r\nb", "endpoint_filter", 256)
