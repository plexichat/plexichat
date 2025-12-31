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
import concurrent.futures
from datetime import datetime


class TestCompleteUserJourney:
    """Test complete user workflows."""

    def test_complete_registration_to_messaging(self, benchmark, modules):
        """Benchmark complete flow: register -> login -> create DM -> send message."""
        counter = [0]
        
        def complete_journey():
            username = f"journey_{counter[0]}"
            email = f"{username}@example.com"
            password = "JourneyTest123!@#"
            counter[0] += 1
            
            user1 = modules.auth.register(username=username, email=email, password=password)
            
            modules.auth.login(username=username, password=password)
            
            username2 = f"journey_{counter[0]}"
            email2 = f"{username2}@example.com"
            counter[0] += 1
            user2 = modules.auth.register(username=username2, email=email2, password=password)
            
            dm = modules.messaging.create_dm(user1.id, user2.id)
            
            message = modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content="Journey test message"
            )
            
            return message
        
        result = benchmark(complete_journey)
        assert result is not None

    def test_server_creation_to_messaging(self, benchmark, modules, user_pool):
        """Benchmark: create server -> add members -> create channel -> send message."""
        def server_journey():
            owner = user_pool.get_user()
            member1 = user_pool.get_user()
            member2 = user_pool.get_user()
            
            server = modules.servers.create_server(
                owner_id=owner.id,
                name="Performance Server"
            )
            
            modules.servers.add_member(server.id, member1.id)
            modules.servers.add_member(server.id, member2.id)
            
            channel = modules.servers.create_channel(
                user_id=owner.id,
                server_id=server.id,
                name="general",
                channel_type="text"
            )
            
            conv_id = channel.conversation_id
            
            message = modules.messaging.send_message(
                user_id=owner.id,
                conversation_id=conv_id,
                content="Server message"
            )
            
            return message
        
        result = benchmark(server_journey)
        assert result is not None


class TestServerPerformance:
    """Test server-specific performance scenarios."""

    def test_large_server_member_operations(self, modules, load_test_server):
        """Test operations on a server with many members."""
        server, owner, members = load_test_server
        
        times = []
        
        for _ in range(20):
            start = datetime.now()
            modules.servers.get_members(owner.id, server.id)
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)
        
        avg_time = sum(times) / len(times)
        assert avg_time < 0.1, f"Get members too slow: {avg_time}s"

    def test_permission_check_performance(self, benchmark, modules, load_test_server):
        """Benchmark permission checking."""
        server, owner, members = load_test_server
        
        channels = modules.servers.get_channels(server.id)
        if not channels:
            pytest.skip("No channels in test server")
        
        channel_id = channels[0].id
        
        def check_permission():
            return modules.servers.get_channel(channel_id, owner.id)
        
        result = benchmark(check_permission)
        assert result is not None

    def test_server_with_many_channels(self, modules, user_pool):
        """Test performance with many channels in a server."""
        owner = user_pool.get_user()
        server = modules.servers.create_server(
            owner_id=owner.id,
            name="Many Channels Server"
        )
        
        channels = []
        for i in range(50):
            channel = modules.servers.create_channel(
                user_id=owner.id,
                server_id=server.id,
                name=f"channel-{i}",
                channel_type="text"
            )
            channels.append(channel)
        
        times = []
        for _ in range(20):
            start = datetime.now()
            channel_list = modules.servers.get_channels(owner.id, server.id)
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)
        
        avg_time = sum(times) / len(times)
        assert avg_time < 0.15, f"Get channels too slow with 50 channels: {avg_time}s"
        assert len(channel_list) == 51


class TestCrossModulePerformance:
    """Test performance of cross-module interactions."""

    def test_relationship_and_messaging(self, benchmark, modules, user_pool):
        """Test relationships + messaging integration."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        
        def relationship_messaging():
            req = modules.relationships.send_friend_request(user1.id, user2.id)
            
            modules.relationships.accept_friend_request(user2.id, req.id)
            
            dm = modules.messaging.create_dm(user1.id, user2.id)
            
            message = modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content="Friend message"
            )
            
            # Cleanup for next round
            modules.relationships.remove_friend(user1.id, user2.id)
            
            return message
        
        result = benchmark(relationship_messaging)
        assert result is not None

    def test_presence_and_messaging(self, modules, test_dm):
        """Test presence updates during messaging."""
        dm, user1, user2 = test_dm
        
        times = []
        
        for i in range(50):
            modules.presence.set_status(user1.id, "online")
            
            start = datetime.now()
            modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content=f"Presence test {i}"
            )
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)
        
        avg_time = sum(times) / len(times)
        assert avg_time < 0.15, f"Send with presence too slow: {avg_time}s"

    def test_notifications_and_messaging(self, modules, test_dm):
        """Test notification generation during messaging."""
        dm, user1, user2 = test_dm
        
        times = []
        
        for i in range(50):
            start = datetime.now()
            modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content=f"Notification test {i}"
            )
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)
        
        avg_time = sum(times) / len(times)
        assert avg_time < 0.15, f"Send with notifications too slow: {avg_time}s"


class TestConcurrentWorkflows:
    """Test concurrent complete workflows."""

    def test_concurrent_user_registrations_and_messaging(self, modules):
        """Test many users registering and messaging concurrently."""
        import uuid
        test_run_id = uuid.uuid4().hex[:6]
        
        def user_workflow(user_id):
            username = f"concurrent_{test_run_id}_{user_id}"
            email = f"{username}@example.com"
            password = "ConcurrentTest123!@#"
            
            user = modules.auth.register(username=username, email=email, password=password)
            
            modules.auth.login(username=username, password=password)
            
            return user
        
        start = datetime.now()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(user_workflow, i)
                for i in range(50)
            ]
            users = [f.result() for f in concurrent.futures.as_completed(futures)]
        
        elapsed = (datetime.now() - start).total_seconds()
        
        assert len(users) == 50
        assert elapsed < 30, f"Concurrent workflow too slow: {elapsed}s"

    def test_concurrent_server_operations(self, modules, load_test_server):
        """Test concurrent operations on the same server."""
        server, owner, members = load_test_server
        
        channels = modules.servers.get_channels(server.id)
        if not channels:
            pytest.skip("No channels in test server")
        
        channel = channels[0]
        conv_id = channel.conversation_id
        
        def send_message(user, msg_num):
            return modules.messaging.send_message(
                user_id=user.id,
                conversation_id=conv_id,
                content=f"Concurrent server message {msg_num}"
            )
        
        start = datetime.now()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for i in range(50):
                user = members[i % len(members)]
                futures.append(executor.submit(send_message, user, i))
            
            messages = [f.result() for f in concurrent.futures.as_completed(futures)]
        
        elapsed = (datetime.now() - start).total_seconds()
        
        assert len(messages) == 50
        assert elapsed < 5, f"Concurrent server messaging too slow: {elapsed}s"


class TestRealWorldScenarios:
    """Test realistic usage patterns."""

    def test_active_conversation_simulation(self, modules, test_dm):
        """Simulate an active conversation with back-and-forth messages."""
        dm, user1, user2 = test_dm
        
        start = datetime.now()
        
        for i in range(100):
            sender = user1 if i % 2 == 0 else user2
            
            modules.messaging.send_message(
                user_id=sender.id,
                conversation_id=dm.id,
                content=f"Active conversation message {i}"
            )
            
            if i % 10 == 0:
                modules.messaging.get_messages(
                    user_id=sender.id,
                    conversation_id=dm.id,
                    limit=10
                )
        
        elapsed = (datetime.now() - start).total_seconds()
        
        assert elapsed < 10, f"Active conversation simulation too slow: {elapsed}s"

    def test_server_with_active_channels(self, modules, load_test_server):
        """Simulate multiple active channels in a server."""
        server, owner, members = load_test_server
        
        channels = []
        for i in range(5):
            channel = modules.servers.create_channel(
                user_id=owner.id,
                server_id=server.id,
                name=f"active-{i}",
                channel_type="text"
            )
            channels.append(channel)
        
        start = datetime.now()
        
        for i in range(100):
            channel = channels[i % len(channels)]
            user = members[i % len(members)]
            
            modules.messaging.send_message(
                user_id=user.id,
                conversation_id=channel.conversation_id,
                content=f"Multi-channel message {i}"
            )
        
        elapsed = (datetime.now() - start).total_seconds()
        
        assert elapsed < 10, f"Multi-channel activity too slow: {elapsed}s"

    def test_peak_load_simulation(self, modules, load_test_server):
        """Simulate peak load with many concurrent operations."""
        server, owner, members = load_test_server
        
        channels = modules.servers.get_channels(server.id)
        if not channels:
            pytest.skip("No channels in test server")
        
        channel = channels[0]
        conv_id = channel.conversation_id
        
        operations = []
        
        for i in range(100):
            user = members[i % len(members)]
            if i % 5 == 0:
                operations.append(('get', user))
            else:
                operations.append(('send', user, f"Peak load message {i}"))
        
        start = datetime.now()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            
            for op in operations:
                if op[0] == 'get':
                    futures.append(
                        executor.submit(
                            modules.messaging.get_messages,
                            op[1].id,
                            conv_id,
                            20
                        )
                    )
                else:
                    futures.append(
                        executor.submit(
                            modules.messaging.send_message,
                            op[1].id,
                            conv_id,
                            op[2]
                        )
                    )
            
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
        elapsed = (datetime.now() - start).total_seconds()
        
        assert len(results) == 100
        assert elapsed < 10, f"Peak load simulation too slow: {elapsed}s"
