"""Tests for gateway rate limiting."""

import pytest
import time
from src.api.websocket.connection import Connection


class TestConnectionRateLimit:
    """Tests for connection-level rate limiting."""

    def test_rate_limit_allows_under_limit(self, connection):
        """Test rate limit allows requests under limit."""
        for _ in range(50):
            assert connection.check_rate_limit(120) is True

    def test_rate_limit_blocks_at_limit(self, connection):
        """Test rate limit blocks at limit."""
        for _ in range(120):
            connection.check_rate_limit(120)

        assert connection.check_rate_limit(120) is False

    def test_rate_limit_resets_after_window(self, connection):
        """Test rate limit resets after time window."""
        for _ in range(120):
            connection.check_rate_limit(120)

        connection.event_window_start = time.monotonic() - 61

        assert connection.check_rate_limit(120) is True
        assert connection.event_count == 1

    def test_rate_limit_tracks_count(self, connection):
        """Test rate limit tracks event count."""
        assert connection.event_count == 0

        for i in range(10):
            connection.check_rate_limit(120)
            assert connection.event_count == i + 1

    def test_rate_limit_custom_limit(self, connection):
        """Test rate limit with custom limit."""
        for _ in range(50):
            connection.check_rate_limit(50)

        assert connection.check_rate_limit(50) is False

    def test_rate_limit_window_start_updates(self, connection):
        """Test rate limit window start updates."""
        old_start = connection.event_window_start

        connection.event_window_start = time.monotonic() - 61
        connection.check_rate_limit(120)

        assert connection.event_window_start > old_start


class TestRateLimitEdgeCases:
    """Tests for rate limit edge cases."""

    def test_rate_limit_exactly_at_limit(self, connection):
        """Test rate limit at exactly the limit."""
        for _ in range(119):
            assert connection.check_rate_limit(120) is True

        assert connection.check_rate_limit(120) is True
        assert connection.check_rate_limit(120) is False

    def test_rate_limit_zero_limit(self, connection):
        """Test rate limit with zero limit."""
        assert connection.check_rate_limit(0) is False

    def test_rate_limit_high_limit(self, connection):
        """Test rate limit with high limit."""
        for _ in range(1000):
            assert connection.check_rate_limit(10000) is True

    def test_rate_limit_multiple_windows(self, connection):
        """Test rate limit across multiple windows."""
        for _ in range(120):
            connection.check_rate_limit(120)
        assert connection.check_rate_limit(120) is False

        connection.event_window_start = time.monotonic() - 61
        for _ in range(120):
            connection.check_rate_limit(120)
        assert connection.check_rate_limit(120) is False

        connection.event_window_start = time.monotonic() - 61
        assert connection.check_rate_limit(120) is True


class TestDispatcherRateLimit:
    """Tests for dispatcher rate limiting."""

    @pytest.mark.asyncio
    async def test_dispatch_respects_rate_limit(self, dispatcher, session_manager, mock_websocket):
        """Test dispatch respects connection rate limit."""
        from src.core.events.types import GatewayIntent
        from src.core import events

        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session_manager.create_session(conn, user_id=12345, intents=GatewayIntent.all_intents())

        for _ in range(120):
            conn.check_rate_limit(120)

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )

        count = await dispatcher.dispatch_event(event, [12345])
        assert count == 0

    @pytest.mark.asyncio
    async def test_dispatch_allows_under_rate_limit(self, dispatcher, session_manager, mock_websocket):
        """Test dispatch allows under rate limit."""
        from src.core.events.types import GatewayIntent
        from src.core import events

        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session_manager.create_session(conn, user_id=12345, intents=GatewayIntent.all_intents())

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )

        count = await dispatcher.dispatch_event(event, [12345])
        assert count == 1
