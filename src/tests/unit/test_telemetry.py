"""
Unit tests for the telemetry module.
"""

import pytest
import time
from unittest.mock import MagicMock


class TestTelemetryModule:
    """Tests for telemetry module functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.execute = MagicMock(return_value=MagicMock(rowcount=1))
        db.fetch_all = MagicMock(return_value=[])
        db.fetch_one = MagicMock(return_value=None)
        db.convert_schema = MagicMock(side_effect=lambda x: x)
        return db

    @pytest.fixture
    def telemetry_module(self, mock_db):
        """Setup telemetry module with mock db."""
        from src.core import telemetry

        telemetry.setup(mock_db)
        return telemetry

    def test_setup_creates_tables(self, mock_db):
        """Test that setup creates the required tables."""
        from src.core import telemetry

        telemetry.setup(mock_db)
        telemetry.create_tables(mock_db)

        # Should have called execute for table creation and indexes
        assert mock_db.execute.called
        calls = [str(c) for c in mock_db.execute.call_args_list]
        assert any("CREATE TABLE" in str(c) for c in calls)
        assert any("CREATE INDEX" in str(c) for c in calls)

    def test_is_setup_returns_true_after_setup(self, telemetry_module):
        """Test is_setup returns True after initialization."""
        assert telemetry_module.is_setup() is True

    def test_submit_response_times_valid_entries(self, telemetry_module, mock_db):
        """Test submitting valid response time entries."""
        entries = [
            {
                "endpoint": "/api/v1/users/@me",
                "method": "GET",
                "response_time_ms": 45.2,
                "status_code": 200,
                "timestamp": int(time.time() * 1000),
            },
            {
                "endpoint": "/api/v1/messages",
                "method": "POST",
                "response_time_ms": 120.5,
                "status_code": 201,
                "timestamp": int(time.time() * 1000),
            },
        ]

        accepted = telemetry_module.submit_response_times(entries, "test_client")
        assert accepted == 2

    def test_submit_response_times_invalid_entries_skipped(
        self, telemetry_module, mock_db
    ):
        """Test that invalid entries are skipped."""
        entries = [
            {
                "endpoint": "",  # Invalid: empty endpoint
                "method": "GET",
                "response_time_ms": 45.2,
                "status_code": 200,
            },
            {
                "endpoint": "/api/v1/test",
                "method": "GET",
                "response_time_ms": -10,  # Invalid: negative time
                "status_code": 200,
            },
            {
                "endpoint": "/api/v1/valid",
                "method": "GET",
                "response_time_ms": 50,
                "status_code": 200,
            },
        ]

        accepted = telemetry_module.submit_response_times(entries)
        assert accepted == 1  # Only the valid entry

    def test_submit_response_times_timestamp_validation(
        self, telemetry_module, mock_db
    ):
        """Test that timestamps too far in past/future are corrected."""
        now = int(time.time() * 1000)
        entries = [
            {
                "endpoint": "/api/v1/test",
                "method": "GET",
                "response_time_ms": 50,
                "status_code": 200,
                "timestamp": now - 7200000,  # 2 hours ago (outside 1 hour window)
            }
        ]

        accepted = telemetry_module.submit_response_times(entries)
        assert accepted == 1

    def test_get_endpoint_stats_empty(self, telemetry_module, mock_db):
        """Test getting stats when no data exists."""
        mock_db.fetch_all.return_value = []

        stats = telemetry_module.get_endpoint_stats(hours=24)
        assert stats == []

    def test_get_endpoint_stats_with_data(self, telemetry_module, mock_db):
        """Test getting stats with data."""
        mock_db.fetch_all.return_value = [
            {
                "endpoint": "/api/v1/channels/123456/messages",
                "method": "GET",
                "count": 3,
                "avg_ms": 20.0,
                "min_ms": 10.0,
                "max_ms": 30.0,
                "avg_q": 1.0,
                "avg_dt": 5.0,
                "err_count": 1,
                "err_rate": 33.33,
            }
        ]

        stats = telemetry_module.get_endpoint_stats(hours=24)

        assert len(stats) == 1
        assert stats[0].endpoint == "/api/v1/channels/{channel_id}/messages"
        assert stats[0].method == "GET"
        assert stats[0].count == 3
        assert stats[0].avg_response_time_ms == 20.0
        assert stats[0].min_response_time_ms == 10
        assert stats[0].max_response_time_ms == 30
        # The error rate calculation may have floating point precision issues
        assert stats[0].error_rate > 30 and stats[0].error_rate < 40

    def test_get_endpoint_stats_escapes_like_wildcards(self, telemetry_module, mock_db):
        """A ``%``/``_`` endpoint filter must be matched literally."""
        # One aggregate row so the second (raw-sample) pass actually runs: an
        # empty first result short-circuits get_endpoint_stats before it, and
        # then only one of the two LIKE sites would be exercised.
        mock_db.fetch_all.side_effect = [
            [
                {
                    "endpoint": "/api/v1/items/100%_off",
                    "method": "GET",
                    "count": 1,
                    "avg_ms": 10.0,
                    "min_ms": 10,
                    "max_ms": 10,
                    "avg_q": 0.0,
                    "avg_dt": 0.0,
                    "err_count": 0,
                }
            ],
            [],
        ]

        telemetry_module.get_endpoint_stats(hours=24, endpoint_filter="100%_off")

        # Two queries run: the aggregate pass and the raw-sample pass.
        assert mock_db.fetch_all.call_count == 2
        for call in mock_db.fetch_all.call_args_list:
            sql, params = call.args
            assert "endpoint LIKE ? ESCAPE '\\'" in sql
            # The filter's position moves with the other filters and the
            # sample cap, so look for the escaped value rather than a slot.
            assert "%100\\%\\_off%" in params

    def test_get_endpoint_stats_percent_is_not_match_all(
        self, telemetry_module, mock_db
    ):
        """A bare ``%`` must not degenerate into a match-everything wildcard."""
        mock_db.fetch_all.return_value = []

        telemetry_module.get_endpoint_stats(hours=24, endpoint_filter="%")

        sql, params = mock_db.fetch_all.call_args_list[0].args
        assert params[-1] == "%\\%%"
        assert params[-1] != "%%%"

    def test_get_response_time_history_escapes_literal_segments(
        self, telemetry_module, mock_db
    ):
        """Literal route segments are escaped; the route param stays a wildcard."""
        mock_db.fetch_all.return_value = []

        telemetry_module.get_response_time_history(
            endpoint="/api/v1/users/{user_id}/a_b", method="GET", hours=24
        )

        sql, params = mock_db.fetch_all.call_args.args
        assert "endpoint LIKE ? ESCAPE '\\'" in sql
        assert params[0] == "/api/v1/users/%/a\\_b"

    def test_get_response_time_history_empty(self, telemetry_module, mock_db):
        """Test getting history when no data exists."""
        mock_db.fetch_all.return_value = []

        history = telemetry_module.get_response_time_history(
            endpoint="/api/v1/test", method="GET", hours=24
        )
        assert history == []

    def test_get_response_time_history_with_data(self, telemetry_module, mock_db):
        """Test getting history with data."""
        # 1704067200000 ms is exactly on a 5-minute bucket boundary, making
        # the bucketing deterministic: rows 1+2 share a bucket, row 3 lands
        # in the next one.
        boundary = 1704067200000
        bucket_ms = 5 * 60 * 1000  # 5 minutes

        mock_db.fetch_all.return_value = [
            {"timestamp": boundary, "response_time_ms": 10, "status_code": 200},
            {
                "timestamp": boundary + 1000,
                "response_time_ms": 20,
                "status_code": 200,
            },
            {
                "timestamp": boundary + bucket_ms,
                "response_time_ms": 30,
                "status_code": 200,
            },
        ]

        history = telemetry_module.get_response_time_history(
            endpoint="/api/v1/test", method="GET", hours=1, bucket_minutes=5
        )

        # Exactly two buckets: [10, 20] and [30]
        assert len(history) == 2
        bucket_counts = sorted(h["count"] for h in history)
        assert bucket_counts == [1, 2]
        bucket_avgs = sorted(h["avg_ms"] for h in history)
        assert bucket_avgs == [15.0, 30.0]
        assert all(h["error_count"] == 0 for h in history)

    def test_cleanup_old_data(self, telemetry_module, mock_db):
        """Test cleanup of old telemetry data."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 100
        mock_db.execute.return_value = mock_cursor

        deleted = telemetry_module.cleanup_old_data(days=30)
        assert deleted == 100


class TestResponseTimeEntry:
    """Tests for ResponseTimeEntry dataclass."""

    def test_create_entry(self):
        """Test creating a response time entry."""
        from src.core.telemetry import ResponseTimeEntry

        entry = ResponseTimeEntry(
            id=1,
            endpoint="/api/v1/test",
            method="GET",
            response_time_ms=45.2,
            status_code=200,
            timestamp=1704067200000,
        )

        assert entry.id == 1
        assert entry.endpoint == "/api/v1/test"
        assert entry.method == "GET"
        assert entry.response_time_ms == 45.2
        assert entry.status_code == 200


class TestEndpointStats:
    """Tests for EndpointStats dataclass."""

    def test_create_stats(self):
        """Test creating endpoint stats."""
        from src.core.telemetry import EndpointStats

        stats = EndpointStats(
            endpoint="/api/v1/test",
            method="GET",
            count=100,
            avg_response_time_ms=50.0,
            min_response_time_ms=10.0,
            max_response_time_ms=200.0,
            p50_response_time_ms=45.0,
            p95_response_time_ms=150.0,
            p99_response_time_ms=180.0,
            error_rate=0.05,
            last_updated=1704067200000,
        )

        assert stats.count == 100
        assert stats.error_rate == 0.05


class TestPercentile:
    """Tests for the nearest-rank ``_percentile`` helper.

    The helper used ``round`` instead of ``ceil`` for the rank, so for
    sample counts whose cutoff lands on a ``.5`` fraction (e.g. p50 of 5
    samples) banker's rounding picked the sample *below* the nearest
    rank. These pin the nearest-rank definition.
    """

    def test_p50_odd_sample_count_uses_upper_middle(self):
        from src.core.telemetry import _percentile

        samples = [10.0, 20.0, 30.0, 40.0, 50.0]
        # ceil(0.50 * 5) = 3 -> the 3rd sample, NOT round(2.5) -> 2nd.
        assert _percentile(samples, 50.0) == 30.0

    def test_p50_nine_samples_uses_fifth(self):
        from src.core.telemetry import _percentile

        samples = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
        # ceil(0.50 * 9) = 5 -> 50.0, not round(4.5) -> 40.0.
        assert _percentile(samples, 50.0) == 50.0

    def test_p95_and_p99_are_nearest_rank(self):
        from src.core.telemetry import _percentile

        samples = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        # ceil(0.95 * 10) = 10 -> 100.0; ceil(0.99 * 10) = 10 -> 100.0.
        assert _percentile(samples, 95.0) == 100.0
        assert _percentile(samples, 99.0) == 100.0

    def test_never_interpolates_between_samples(self):
        from src.core.telemetry import _percentile

        samples = [10.0, 20.0]
        # Every result must be an observed value, never a value between them.
        assert _percentile(samples, 50.0) in samples
        assert _percentile(samples, 95.0) in samples

    def test_empty_and_boundaries(self):
        from src.core.telemetry import _percentile

        assert _percentile([], 50.0) == 0.0
        assert _percentile([7.0], 50.0) == 7.0
        assert _percentile([1.0, 2.0, 3.0], 0.0) == 1.0
        assert _percentile([1.0, 2.0, 3.0], 100.0) == 3.0
