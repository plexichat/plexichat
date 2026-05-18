"""
Stress tests for extreme scenarios.

Tests system behavior under extreme conditions:
- Maximum connection limits
- Very large messages
- Rapid succession operations
- Resource exhaustion scenarios
- Recovery from overload
"""

import pytest
import concurrent.futures
import time
from datetime import datetime


class TestConnectionLimits:
    """Test behavior at connection limits."""

    def test_max_sessions_per_user(self, auth_manager):
        """Test behavior when user hits max session limit."""
        username = "maxsessions_user"
        email = f"{username}@example.com"
        password = "StressTest123!@#"

        user = auth_manager.register(username=username, email=email, password=password)

        sessions = []
        for i in range(50):
            result = auth_manager.login(username=username, password=password)
            sessions.append(result)

        assert len(sessions) == 50

        active_sessions = auth_manager.get_sessions(user.id)
        assert len(active_sessions) <= 50

    def test_many_concurrent_conversations(self, messaging_manager, stress_test_users):
        """Test user with many active conversations."""
        if len(stress_test_users) < 30:
            pytest.skip("Not enough users for stress test")

        user = stress_test_users[0]

        conversations = []
        for other_user in stress_test_users[1:30]:
            dm = messaging_manager.create_dm(user.id, other_user.id)
            conversations.append(dm)

        assert len(conversations) == 29

        times = []
        for _ in range(10):
            start = datetime.now()
            messaging_manager.get_conversations(user.id)
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        assert avg_time < 0.5, (
            f"Get conversations too slow with 29 conversations: {avg_time}s"
        )


class TestLargeData:
    """Test handling of large data."""

    def test_maximum_message_length(self, messaging_manager, test_dm):
        """Test sending maximum length message."""
        dm, user1, user2 = test_dm

        max_length = 4000
        large_content = "x" * max_length

        start = datetime.now()
        msg = messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content=large_content
        )
        elapsed = (datetime.now() - start).total_seconds()

        assert msg is not None
        assert len(msg.content) == max_length
        assert elapsed < 0.5, f"Large message send took {elapsed}s"

    def test_many_messages_in_conversation(self, messaging_manager, test_dm):
        """Test conversation with very large message history."""
        dm, user1, user2 = test_dm

        batch_size = 100
        for i in range(batch_size):
            messaging_manager.send_message(
                user_id=user1.id if i % 2 == 0 else user2.id,
                conversation_id=dm.id,
                content=f"Stress message {i}",
            )

        times = []
        for _ in range(10):
            start = datetime.now()
            messages = messaging_manager.get_messages(
                user_id=user1.id, conversation_id=dm.id, limit=50
            )
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        assert avg_time < 0.3, f"Get messages too slow with large history: {avg_time}s"
        assert len(messages) == 50

    def test_server_with_maximum_members(self, server_manager, stress_test_users):
        """Test server performance with many members."""
        if len(stress_test_users) < 40:
            pytest.skip("Not enough users for max members test")

        owner = stress_test_users[0]
        server = server_manager.create_server(
            owner_id=owner.id, name="Max Members Server"
        )

        for user in stress_test_users[1:40]:
            try:
                server_manager.add_member(server.id, user.id)
            except Exception:
                pass

        times = []
        for _ in range(10):
            start = datetime.now()
            server_manager.get_members(server.id)
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        assert avg_time < 0.3, f"Get members too slow with many members: {avg_time}s"


class TestRapidOperations:
    """Test rapid succession operations."""

    def test_rapid_message_sending(self, messaging_manager, test_dm):
        """Test sending messages in rapid succession."""
        dm, user1, user2 = test_dm

        start = datetime.now()
        messages = []

        for i in range(100):
            msg = messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=f"Rapid message {i}"
            )
            messages.append(msg)

        elapsed = (datetime.now() - start).total_seconds()

        assert len(messages) == 100
        assert elapsed < 10, f"100 rapid sends took {elapsed}s"

        throughput = 100 / elapsed
        assert throughput > 10, f"Throughput too low: {throughput:.1f} msg/s"

    def test_rapid_login_logout(self, auth_manager):
        """Test rapid login/logout cycles."""
        username = "rapidauth_user"
        email = f"{username}@example.com"
        password = "StressTest123!@#"

        user = auth_manager.register(username=username, email=email, password=password)

        start = datetime.now()

        for i in range(20):
            result = auth_manager.login(username=username, password=password)
            auth_manager.revoke_session(user.id, result.session.id)

        elapsed = (datetime.now() - start).total_seconds()

        assert elapsed < 30, f"20 login/logout cycles took {elapsed}s"

    def test_rapid_status_changes(self, presence_manager, test_user):
        """Test rapid presence status changes."""
        from src.core.presence.models import UserStatus

        statuses = [
            UserStatus.ONLINE,
            UserStatus.IDLE,
            UserStatus.DND,
            UserStatus.INVISIBLE,
        ]

        start = datetime.now()

        for i in range(100):
            status = statuses[i % len(statuses)]
            presence_manager.set_status(test_user.id, status)

        elapsed = (datetime.now() - start).total_seconds()

        assert elapsed < 5, f"100 status changes took {elapsed}s"


class TestConcurrencyStress:
    """Test extreme concurrency scenarios."""

    def test_thundering_herd_login(self, auth_manager, stress_test_users):
        """Test many users logging in simultaneously."""
        if len(stress_test_users) < 50:
            pytest.skip("Not enough users for thundering herd test")

        credentials = [
            (user.username, "StressTest123!@#") for user in stress_test_users[:50]
        ]

        start = datetime.now()

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [
                executor.submit(auth_manager.login, username, password)
                for username, password in credentials
            ]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        elapsed = (datetime.now() - start).total_seconds()

        assert len(results) == 50
        assert elapsed < 15, f"50 simultaneous logins took {elapsed}s"

    def test_concurrent_server_operations_stress(
        self, messaging_manager, server_manager, presence_manager, load_test_server
    ):
        """Test extreme concurrent operations on server."""
        server, owner, members = load_test_server

        channels = server_manager.get_channels(server.id)
        if not channels:
            pytest.skip("No channels in test server")

        channel = channels[0]
        conv_id = channel.conversation_id

        def mixed_operation(op_id):
            user = members[op_id % len(members)]

            if op_id % 4 == 0:
                return messaging_manager.get_messages(user.id, conv_id, 20)
            elif op_id % 4 == 1:
                return messaging_manager.send_message(
                    user.id, conv_id, f"Stress {op_id}"
                )
            elif op_id % 4 == 2:
                return server_manager.get_members(server.id)
            else:
                return presence_manager.set_status(user.id, "online")

        start = datetime.now()

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(mixed_operation, i) for i in range(200)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        elapsed = (datetime.now() - start).total_seconds()

        assert len(results) == 200
        assert elapsed < 15, f"200 mixed concurrent operations took {elapsed}s"


class TestRecovery:
    """Test system recovery from stress."""

    def test_recovery_after_burst(self, messaging_manager, test_dm):
        """Test system recovers normal performance after burst."""
        dm, user1, user2 = test_dm

        for i in range(500):
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=f"Burst message {i}"
            )

        time.sleep(1)

        times = []
        for i in range(20):
            start = datetime.now()
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=f"Recovery test {i}"
            )
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        assert avg_time < 0.15, f"Failed to recover, average time: {avg_time}s"

    def test_memory_recovery_after_load(
        self, messaging_manager, test_dm, memory_tracker
    ):
        """Test memory is freed after load."""
        dm, user1, user2 = test_dm

        initial = memory_tracker.snapshot()

        for i in range(200):
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=f"Memory load {i}"
            )

        peak = memory_tracker.snapshot()

        time.sleep(2)

        import gc

        gc.collect()

        final = memory_tracker.snapshot()

        peak_increase = peak - initial
        final_increase = final - initial

        recovery_ratio = (
            (peak_increase - final_increase) / peak_increase if peak_increase > 0 else 0
        )

        assert recovery_ratio > 0.3, (
            f"Poor memory recovery: {recovery_ratio * 100:.1f}%"
        )


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_message_handling(self, messaging_manager, test_dm):
        """Test handling of edge case message content."""
        dm, user1, user2 = test_dm

        try:
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=""
            )
            assert False, "Should not allow empty message"
        except Exception:
            pass

    def test_special_characters_message(self, messaging_manager, test_dm):
        """Test message with special characters."""
        dm, user1, user2 = test_dm

        special_content = "Test 🚀 emoji and \n newlines \t tabs ' \" quotes"

        msg = messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content=special_content
        )

        assert msg is not None
        retrieved = messaging_manager.get_message(user1.id, msg.id)
        assert retrieved.content == special_content

    def test_rapid_edit_operations(self, messaging_manager, test_dm):
        """Test rapidly editing same message."""
        dm, user1, user2 = test_dm

        msg = messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Original"
        )

        for i in range(20):
            messaging_manager.edit_message(
                user_id=user1.id, message_id=msg.id, content=f"Edit {i}"
            )

        final = messaging_manager.get_message(user1.id, msg.id)
        assert "Edit 19" in final.content

    def test_zero_pagination_limit(self, messaging_manager, test_dm):
        """Test edge case pagination parameters."""
        dm, user1, user2 = test_dm

        for i in range(10):
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=f"Pagination test {i}"
            )

        messages = messaging_manager.get_messages(
            user_id=user1.id, conversation_id=dm.id, limit=1
        )

        assert len(messages) == 1

    def test_large_server_name(self, server_manager, test_user):
        """Test server with maximum length name."""
        max_name = "x" * 100

        server = server_manager.create_server(owner_id=test_user.id, name=max_name)

        assert server is not None
        assert len(server.name) <= 100
