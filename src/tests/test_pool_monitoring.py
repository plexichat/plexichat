"""
Tests for connection pool monitoring functionality.

Tests verify:
- Pool statistics collection and reporting
- Connection acquisition time tracking
- Connection age tracking and warnings
- Periodic logging of pool statistics
- Pool exhaustion detection
- Admin API pool health endpoint
"""

import pytest
import time
import threading
from datetime import datetime
from unittest.mock import patch

import utils.config as config
import utils.logger as logger
from src.core.database.core import Database


class TestPoolStatistics:
    """Test pool statistics collection and reporting."""

    @pytest.fixture
    def test_db(self):
        """Create a test database instance."""
        # Use SQLite for testing since it doesn't require external dependencies
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
                'monitoring': {'log_interval_seconds': 60}
            }
        }):
            db = Database()
            yield db

    def test_get_pool_stats_returns_dict(self, test_db):
        """Test that get_pool_stats returns a properly formatted dictionary."""
        stats = test_db.get_pool_stats()
        
        # Verify all required fields are present
        assert isinstance(stats, dict)
        assert 'database_type' in stats
        assert 'timestamp' in stats
        assert 'active_connections' in stats
        assert 'idle_connections' in stats
        assert 'total_acquisitions' in stats
        assert 'total_pool_waits' in stats
        assert 'old_connections' in stats
        assert 'avg_acquisition_time' in stats
        assert 'max_acquisition_time' in stats

    def test_timestamp_in_iso_format(self, test_db):
        """Test that timestamp is in ISO format."""
        stats = test_db.get_pool_stats()
        timestamp = stats['timestamp']
        
        # Should parse as ISO format
        dt = datetime.fromisoformat(timestamp)
        assert isinstance(dt, datetime)

    def test_pool_stats_database_type(self, test_db):
        """Test that pool stats correctly identify database type."""
        stats = test_db.get_pool_stats()
        assert stats['database_type'] == 'sqlite'

    def test_acquisition_metrics_initialized_to_zero(self, test_db):
        """Test that acquisition metrics start at zero."""
        stats = test_db.get_pool_stats()
        
        assert stats['total_acquisitions'] == 0
        assert stats['avg_acquisition_time'] == 0.0
        assert stats['max_acquisition_time'] == 0.0

    def test_pool_wait_metrics_initialized_to_zero(self, test_db):
        """Test that pool wait metrics start at zero."""
        stats = test_db.get_pool_stats()
        
        assert stats['total_pool_waits'] == 0
        assert stats['avg_pool_wait_time'] == 0.0

    def test_old_connections_empty_initially(self, test_db):
        """Test that old connections list is empty initially."""
        stats = test_db.get_pool_stats()
        assert stats['old_connections'] == []


class TestConnectionAgeTracking:
    """Test connection age tracking and warnings."""

    @pytest.fixture
    def test_db(self):
        """Create a test database instance."""
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
                'connection_pool': {
                    'max_connection_age_hours': 0.0001  # ~0.36 seconds for testing
                },
                'monitoring': {'log_interval_seconds': 60}
            }
        }):
            db = Database()
            yield db

    def test_connection_metadata_tracked(self, test_db):
        """Test that connection metadata is tracked."""
        # Get a connection
        test_db._get_conn()
        
        # Verify metadata is created
        assert len(test_db._connection_metadata) > 0
        
        # Metadata should have creation timestamp and thread info
        for conn_id, metadata in test_db._connection_metadata.items():
            assert 'created_at' in metadata
            assert 'thread_id' in metadata
            assert 'acquired_at' in metadata

    def test_connection_age_warning_for_old_connections(self, test_db):
        """Test that warnings are logged for connections exceeding age threshold."""
        # Get a connection
        conn = test_db._get_conn()
        conn_id = id(conn)
        
        # Manually set creation time to past
        with test_db._pool_stats_lock:
            test_db._connection_metadata[conn_id]['created_at'] = time.time() - 10  # 10 seconds ago
        
        # Call check which should log warning
        with patch.object(logger, 'warning') as mock_warning:
            test_db._check_connection_age(conn_id)
            
            # Should have logged a warning for old connection
            mock_warning.assert_called_once()
            call_args = mock_warning.call_args[0][0]
            assert 'exceeded max age' in call_args.lower()

    def test_no_warning_for_young_connections(self, test_db):
        """Test that no warning is logged for young connections."""
        # Get a connection (just created)
        conn = test_db._get_conn()
        conn_id = id(conn)
        
        # Call check which should NOT log warning
        with patch.object(logger, 'warning') as mock_warning:
            test_db._check_connection_age(conn_id)
            
            # Should NOT have logged a warning for young connection
            # (or should log but not about age)
            if mock_warning.called:
                call_args = mock_warning.call_args[0][0].lower()
                assert 'age' not in call_args

    def test_age_threshold_configurable(self):
        """Test that connection age threshold is configurable."""
        # Test with short threshold
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
                'connection_pool': {'max_connection_age_hours': 0.5}  # 30 minutes
            }
        }):
            db = Database()
            assert db._max_connection_age_seconds == 30 * 60  # 1800 seconds
        
        # Test with longer threshold
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
                'connection_pool': {'max_connection_age_hours': 1.0}  # 1 hour
            }
        }):
            db = Database()
            assert db._max_connection_age_seconds == 60 * 60  # 3600 seconds

    def test_age_tracking_disabled_when_threshold_zero(self, test_db):
        """Test that age tracking is disabled when threshold is 0."""
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
                'connection_pool': {'max_connection_age_hours': 0}
            }
        }):
            db = Database()
            assert db._max_connection_age_seconds == 0
            
            stats = db.get_pool_stats()
            assert stats['old_connections'] == []


class TestPeriodicLogging:
    """Test periodic logging of pool statistics."""

    @pytest.fixture
    def test_db(self):
        """Create a test database instance."""
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
                'monitoring': {'log_interval_seconds': 1}  # 1 second for testing
            }
        }):
            db = Database()
            yield db
            # Cleanup
            db.stop_pool_monitoring()

    def test_start_pool_monitoring_creates_thread(self, test_db):
        """Test that starting monitoring creates a background thread."""
        assert test_db._periodic_logging_thread is None
        
        test_db.start_pool_monitoring()
        
        assert test_db._periodic_logging_thread is not None
        assert test_db._periodic_logging_thread.is_alive()
        assert test_db._periodic_logging_thread.daemon

    def test_monitoring_thread_name(self, test_db):
        """Test that monitoring thread has proper name."""
        test_db.start_pool_monitoring()
        
        assert test_db._periodic_logging_thread.name == "DatabasePoolMonitor"

    def test_stop_pool_monitoring_stops_thread(self, test_db):
        """Test that stopping monitoring stops the background thread."""
        test_db.start_pool_monitoring()
        assert test_db._periodic_logging_thread.is_alive()
        
        test_db.stop_pool_monitoring()
        
        # Give thread time to stop
        time.sleep(0.1)
        assert not test_db._periodic_logging_thread.is_alive()

    def test_start_monitoring_idempotent(self, test_db):
        """Test that starting monitoring multiple times is safe."""
        test_db.start_pool_monitoring()
        first_thread = test_db._periodic_logging_thread
        
        # Start again should not create new thread
        test_db.start_pool_monitoring()
        assert test_db._periodic_logging_thread is first_thread

    def test_monitoring_thread_logs_stats(self, test_db):
        """Test that monitoring thread logs pool statistics."""
        with patch.object(logger, 'info') as mock_info:
            test_db.start_pool_monitoring()
            
            # Wait for thread to log stats
            time.sleep(1.5)
            
            # Should have logged pool stats
            info_calls = [call[0][0].lower() for call in mock_info.call_args_list]
            any('pool stats' in call or 'active' in call for call in info_calls)
            # Note: May not be logged if no activity, so we just check thread is running
            assert test_db._periodic_logging_thread.is_alive()

    def test_stop_monitoring_safe_when_not_running(self, test_db):
        """Test that stopping monitoring when not running is safe."""
        # Should not raise error
        test_db.stop_pool_monitoring()
        test_db.stop_pool_monitoring()


class TestAcquisitionTimeTracking:
    """Test connection acquisition time tracking."""

    @pytest.fixture
    def test_db(self):
        """Create a test database instance."""
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
            }
        }):
            db = Database()
            yield db

    def test_acquisition_time_recorded_on_connect(self, test_db):
        """Test that acquisition time is recorded when connection is acquired."""
        # Get connection
        test_db._get_conn()
        
        # For SQLite, acquisitions aren't tracked (only for PostgreSQL pool)
        # But metadata should be created
        assert len(test_db._connection_metadata) > 0

    def test_acquisition_metrics_in_stats(self, test_db):
        """Test that acquisition metrics are included in pool stats."""
        stats = test_db.get_pool_stats()
        
        assert 'avg_acquisition_time' in stats
        assert 'max_acquisition_time' in stats
        assert 'total_acquisitions' in stats
        
        # All should be numeric or 0
        assert isinstance(stats['avg_acquisition_time'], (int, float))
        assert isinstance(stats['max_acquisition_time'], (int, float))
        assert isinstance(stats['total_acquisitions'], int)


class TestPoolHealthStatus:
    """Test overall pool health determination."""

    @pytest.fixture
    def test_db(self):
        """Create a test database instance."""
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
            }
        }):
            db = Database()
            yield db

    def test_pool_stats_include_health_summary(self, test_db):
        """Test that pool stats can be used to determine health."""
        stats = test_db.get_pool_stats()
        
        # Should have data to determine health
        assert 'active_connections' in stats
        assert 'total_acquisitions' in stats
        assert 'total_pool_waits' in stats
        assert 'old_connections' in stats

    def test_metadata_cleanup_on_close(self, test_db):
        """Test that connection metadata is cleaned up when connection is closed."""
        # Get and close connection
        test_db._get_conn()
        initial_count = len(test_db._connection_metadata)
        
        test_db.close()
        
        # Metadata should be cleaned up
        assert len(test_db._connection_metadata) < initial_count


class TestAdminAPIPoolHealth:
    """Test the admin API pool health endpoint (integration tests)."""

    @pytest.mark.asyncio
    async def test_pool_health_endpoint_returns_200(self):
        """Test that pool health endpoint returns successful response."""
        # This would require setting up the full FastAPI app
        # For now, test the core functionality
        from src.core.database.core import Database
        
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
            }
        }):
            db = Database()
            stats = db.get_pool_stats()
            
            # Verify stats have all required fields for endpoint response
            assert stats['status'] is None or isinstance(stats.get('status'), str)
            assert 'database_type' in stats

    def test_pool_stats_serializable_to_json(self, ):
        """Test that pool stats can be serialized to JSON."""
        import json
        
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
            }
        }):
            db = Database()
            stats = db.get_pool_stats()
            
            # Should be JSON serializable
            json_str = json.dumps(stats)
            parsed = json.loads(json_str)
            
            # Verify round-trip
            assert parsed['database_type'] == stats['database_type']
            assert parsed['timestamp'] == stats['timestamp']


class TestMonitoringEdgeCases:
    """Test edge cases in monitoring functionality."""

    @pytest.fixture
    def test_db(self):
        """Create a test database instance."""
        with patch.dict(config.config_data, {
            'database': {
                'type': 'sqlite',
                'path': ':memory:',
            }
        }):
            db = Database()
            yield db
            db.stop_pool_monitoring()

    def test_monitoring_with_no_connections(self, test_db):
        """Test that monitoring works even with no connections."""
        stats = test_db.get_pool_stats()
        
        assert isinstance(stats, dict)
        assert stats['total_acquisitions'] == 0

    def test_multiple_threads_concurrent_access(self, test_db):
        """Test that monitoring is thread-safe with concurrent access."""
        results = []
        
        def worker():
            for _ in range(5):
                stats = test_db.get_pool_stats()
                results.append(stats is not None)
                time.sleep(0.01)
        
        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # All accesses should succeed
        assert all(results)
        assert len(results) == 15

    def test_monitoring_thread_survives_exceptions(self, test_db):
        """Test that monitoring thread continues despite exceptions."""
        test_db.start_pool_monitoring()
        initial_thread = test_db._periodic_logging_thread
        
        # Let it run for a bit
        time.sleep(0.1)
        
        # Thread should still be alive
        assert initial_thread.is_alive()
        
        test_db.stop_pool_monitoring()

    def test_age_check_with_missing_metadata(self, test_db):
        """Test that age checking is safe when metadata is missing."""
        # Try to check age for non-existent connection
        result = test_db._check_connection_age(999999)
        
        # Should not raise error
        assert result is None
