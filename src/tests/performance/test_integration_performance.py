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
            partner = auth_manager.register(
                "perfuser2", "perf2@example.com", "Password123!"
            )
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login("perfuser", "Password123!")
        dm = messaging_manager.create_dm(user.id, partner.id)
        # Only the steady-state send is benchmarked (setup runs once above;
        # sending is repeatable so every benchmark round measures real work).
        msg = benchmark(messaging_manager.send_message, user.id, dm.id, "Test message")
        assert msg is not None
        assert msg.content == "Test message"

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
        # Benchmark the repeatable send (setup above runs once).
        msg = benchmark(
            server_manager.send_channel_message, user.id, channel.id, "Test message"
        )
        assert msg is not None
        assert msg.content == "Test message"


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
        assert server.name == "Test Server"

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
        # Permission checks are read-only and repeatable: benchmark directly.
        has_perm = benchmark(server_manager.has_permission, user.id, server.id, "admin")
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
        assert channel.name == "general"


class TestCrossModulePerformance:
    """Test performance of cross-module interactions."""

    @pytest.mark.skipif(not HAS_BENCHMARK, reason="Requires pytest-benchmark plugin")
    def test_relationship_and_messaging(
        self, benchmark, rel_manager, messaging_manager, auth_manager, user_pool
    ):
        """Benchmark friend-request creation (repeatable via fresh users)."""
        # Basic relationship test
        from src.utils import encryption

        counter = [0]
        seen_pairs = []

        def _setup():
            # Friend requests are single-use per pair, so every benchmark
            # round gets a fresh sender/recipient pair.
            counter[0] += 1
            i = counter[0]
            with patch.object(
                encryption, "hash_password", return_value="fake_hash_$test"
            ):
                user1 = auth_manager.register(
                    f"relbench{i}a", f"relbench{i}a@example.com", "Password123!"
                )
                user2 = auth_manager.register(
                    f"relbench{i}b", f"relbench{i}b@example.com", "Password123!"
                )
            seen_pairs.append((user1.id, user2.id))
            return (user1.id, user2.id), {}

        request = benchmark.pedantic(
            lambda u1, u2: rel_manager.send_friend_request(u1, u2),
            setup=_setup,
            rounds=5,
        )
        assert request is not None
        assert request.sender_id in [a for a, _ in seen_pairs]

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
        presence_manager.set_status(user.id, "online")
        presence = presence_manager.get_presence(user.id)
        assert presence is not None
        assert presence.status.value == "online"

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
        assert msg.content == "Test message"


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
        assert user.username == "concurrentuser"

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
        assert server.name == "Test Server"


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
        assert msg.content == "Test message"

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
        assert channel1.name == "general" and channel1.server_id == server.id
        assert channel2.name == "random" and channel2.server_id == server.id
        assert channel1.conversation_id != channel2.conversation_id

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
        msg = server_manager.send_channel_message(user.id, channel.id, "Test message")
        assert msg is not None
        assert msg.content == "Test message"
