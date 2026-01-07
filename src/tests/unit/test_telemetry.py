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
        # Mock endpoint list
        mock_db.fetch_all.side_effect = [
            [{"endpoint": "/api/v1/test", "method": "GET"}],
            [
                {"response_time_ms": 10, "status_code": 200},
                {"response_time_ms": 20, "status_code": 200},
                {"response_time_ms": 30, "status_code": 500},
            ],
        ]

        stats = telemetry_module.get_endpoint_stats(hours=24)

        assert len(stats) == 1
        assert stats[0].endpoint == "/api/v1/test"
        assert stats[0].method == "GET"
        assert stats[0].count == 3
        assert stats[0].avg_response_time_ms == 20.0
        assert stats[0].min_response_time_ms == 10
        assert stats[0].max_response_time_ms == 30
        assert stats[0].error_rate == pytest.approx(1 / 3)

    def test_get_response_time_history_empty(self, telemetry_module, mock_db):
        """Test getting history when no data exists."""
        mock_db.fetch_all.return_value = []

        history = telemetry_module.get_response_time_history(
            endpoint="/api/v1/test", method="GET", hours=24
        )
        assert history == []

    def test_get_response_time_history_with_data(self, telemetry_module, mock_db):
        """Test getting history with data."""
        now = int(time.time() * 1000)
        bucket_ms = 5 * 60 * 1000  # 5 minutes

        mock_db.fetch_all.return_value = [
            {"timestamp": now - bucket_ms, "response_time_ms": 10},
            {"timestamp": now - bucket_ms + 1000, "response_time_ms": 20},
            {"timestamp": now, "response_time_ms": 30},
        ]

        history = telemetry_module.get_response_time_history(
            endpoint="/api/v1/test", method="GET", hours=1, bucket_minutes=5
        )

        assert len(history) >= 1

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
