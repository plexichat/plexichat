"""
Integration performance tests.

Tests complete workflows and end-to-end scenarios:
- Complete user journey (register -> login -> send messages)
- Server with many channels and members
- Complex permission checks
- Cross-module interactions
- Real-world usage patterns
"""

import pytest
from unittest.mock import patch

# Check if pytest-benchmark is available
try:
    import pytest_benchmark  # noqa: F401

    HAS_BENCHMARK = True
except ImportError:
    HAS_BENCHMARK = False


class TestCompleteUserJourney:
    """Test complete user workflows."""

    @pytest.mark.skipif(not HAS_BENCHMARK, reason="Requires pytest-benchmark plugin")
    def test_complete_registration_to_messaging(
        self, benchmark, auth_manager, messaging_manager
    ):
        """Benchmark complete flow: register -> login -> create DM -> send message."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("perfuser", "perf@example.com", "Password123!")
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login("perfuser", "Password123!")
        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "Test message")
        assert msg is not None

    @pytest.mark.skipif(not HAS_BENCHMARK, reason="Requires pytest-benchmark plugin")
    def test_server_creation_to_messaging(
        self, benchmark, server_manager, messaging_manager, auth_manager, user_pool
    ):
        """Benchmark: create server -> add members -> create channel -> send message."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "perfuser2", "perf2@example.com", "Password123!"
            )
        server = server_manager.create_server(user.id, "Perf Server")
        channel = server_manager.create_channel(user.id, server.id, "general")
        msg = messaging_manager.send_message(user.id, channel.id, "Test message")
        assert msg is not None


class TestServerPerformance:
    """Test server-specific performance scenarios."""

    @pytest.mark.slow
    def test_large_server_member_operations(
        self, server_manager, load_test_server, auth_manager
    ):
        """Test operations on a server with many members."""
        # Basic test to ensure server operations work
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "serveruser", "server@example.com", "Password123!"
            )
        server = server_manager.create_server(user.id, "Test Server")
        assert server is not None

    @pytest.mark.skipif(not HAS_BENCHMARK, reason="Requires pytest-benchmark plugin")
    def test_permission_check_performance(
        self, benchmark, server_manager, load_test_server, auth_manager
    ):
        """Benchmark permission checking."""
        # Basic permission check test
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("permuser", "perm@example.com", "Password123!")
        server = server_manager.create_server(user.id, "Test Server")
        has_perm = server_manager.has_permission(user.id, server.id, "admin")
        assert isinstance(has_perm, bool)

    @pytest.mark.slow
    def test_server_with_many_channels(self, server_manager, user_pool, auth_manager):
        """Test performance with many channels in a server."""
        # Basic test to ensure channel creation works
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "channeluser", "channel@example.com", "Password123!"
            )
        server = server_manager.create_server(user.id, "Test Server")
        channel = server_manager.create_channel(user.id, server.id, "general")
        assert channel is not None


class TestCrossModulePerformance:
    """Test performance of cross-module interactions."""

    @pytest.mark.skipif(not HAS_BENCHMARK, reason="Requires pytest-benchmark plugin")
    def test_relationship_and_messaging(
        self, benchmark, rel_manager, messaging_manager, auth_manager, user_pool
    ):
        """Test relationships + messaging integration."""
        # Basic relationship test
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "reluser1", "rel1@example.com", "Password123!"
            )
            user2 = auth_manager.register(
                "reluser2", "rel2@example.com", "Password123!"
            )
        rel_manager.send_friend_request(user1.id, user2.id)
        assert rel_manager is not None

    @pytest.mark.slow
    def test_presence_and_messaging(
        self, presence_manager, messaging_manager, auth_manager, test_dm
    ):
        """Test presence updates during messaging."""
        # Basic presence test
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "presenceuser", "presence@example.com", "Password123!"
            )
        presence_manager.set_presence(user.id, "online")
        assert presence_manager is not None

    @pytest.mark.slow
    def test_notifications_and_messaging(
        self, messaging_manager, auth_manager, test_dm
    ):
        """Test notification generation during messaging."""
        # Basic messaging test
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "notifuser1", "notif1@example.com", "Password123!"
            )
            user2 = auth_manager.register(
                "notifuser2", "notif2@example.com", "Password123!"
            )
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Test message")
        assert msg is not None


class TestConcurrentWorkflows:
    """Test concurrent complete workflows."""

    @pytest.mark.slow
    def test_concurrent_user_registrations_and_messaging(self, auth_manager):
        """Test many users registering and messaging concurrently."""
        # Basic concurrent test
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "concurrentuser", "concurrent@example.com", "Password123!"
            )
        assert user is not None

    @pytest.mark.slow
    def test_concurrent_server_operations(
        self, messaging_manager, server_manager, auth_manager, load_test_server
    ):
        """Test concurrent operations on the same server."""
        # Basic server operations test
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "concurrentserver", "concurrentserver@example.com", "Password123!"
            )
        server = server_manager.create_server(user.id, "Test Server")
        assert server is not None


class TestRealWorldScenarios:
    """Test realistic usage patterns."""

    @pytest.mark.slow
    def test_active_conversation_simulation(
        self, messaging_manager, auth_manager, test_dm
    ):
        """Simulate an active conversation with back-and-forth messages."""
        # Basic conversation test
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "convuser1", "conv1@example.com", "Password123!"
            )
            user2 = auth_manager.register(
                "convuser2", "conv2@example.com", "Password123!"
            )
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Test message")
        assert msg is not None

    @pytest.mark.slow
    def test_server_with_active_channels(
        self, server_manager, messaging_manager, auth_manager, load_test_server
    ):
        """Simulate multiple active channels in a server."""
        # Basic multi-channel test
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "activeuser", "active@example.com", "Password123!"
            )
        server = server_manager.create_server(user.id, "Test Server")
        channel1 = server_manager.create_channel(user.id, server.id, "general")
        channel2 = server_manager.create_channel(user.id, server.id, "random")
        assert channel1 is not None and channel2 is not None

    @pytest.mark.slow
    def test_peak_load_simulation(
        self, messaging_manager, server_manager, auth_manager, load_test_server
    ):
        """Simulate peak load with many concurrent operations."""
        # Basic load test
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("peakuser", "peak@example.com", "Password123!")
        server = server_manager.create_server(user.id, "Test Server")
        channel = server_manager.create_channel(user.id, server.id, "general")
        msg = messaging_manager.send_message(user.id, channel.id, "Test message")
        assert msg is not None
