"""
Messaging performance and load tests.

Tests messaging critical paths:
- Message sending performance
- Message retrieval with pagination
- Bulk message operations
- Concurrent message sending
- Attachment handling
- Memory leaks in messaging
"""

import concurrent.futures
from datetime import datetime


class TestMessagingPerformance:
    """Test messaging performance."""

    def test_send_message_performance(self, benchmark, modules, test_dm):
        """Benchmark sending a simple text message."""
        dm, user1, user2 = test_dm
        
        def send_message():
            return modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content="Performance test message"
            )
        
        result = benchmark(send_message)
        assert result is not None
        assert result.content == "Performance test message"

    def test_get_messages_performance(self, benchmark, modules, test_dm):
        """Benchmark retrieving messages from a conversation."""
        dm, user1, user2 = test_dm
        
        for i in range(100):
            modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content=f"Message {i}"
            )
        
        def get_messages():
            return modules.messaging.get_messages(
                user_id=user1.id,
                conversation_id=dm.id,
                limit=50
            )
        
        messages = benchmark(get_messages)
        assert len(messages) == 50

    def test_bulk_send_performance(self, benchmark, modules, test_dm):
        """Test sending many messages in sequence."""
        dm, user1, user2 = test_dm
        
        def bulk_send():
            messages = []
            for i in range(100):
                msg = modules.messaging.send_message(
                    user_id=user1.id,
                    conversation_id=dm.id,
                    content=f"Bulk message {i}"
                )
                messages.append(msg)
            return messages
        
        messages = benchmark(bulk_send)
        assert len(messages) == 100

    def test_concurrent_send_performance(self, benchmark, modules, test_dm):
        """Test concurrent message sending."""
        dm, user1, user2 = test_dm
        
        def concurrent_send():
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(
                        modules.messaging.send_message,
                        user1.id,
                        dm.id,
                        f"Concurrent message {i}"
                    )
                    for i in range(50)
                ]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]
            return results
        
        messages = benchmark(concurrent_send)
        assert len(messages) == 50

    def test_message_search_performance(self, benchmark, modules, test_dm):
        """Test message search performance."""
        dm, user1, user2 = test_dm
        
        for i in range(200):
            content = f"Searchable message {i} with keyword" if i % 10 == 0 else f"Regular message {i}"
            modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content=content
            )
        
        def search_messages():
            all_messages = modules.messaging.get_messages(
                user_id=user1.id,
                conversation_id=dm.id,
                limit=500
            )
            return [m for m in all_messages if "keyword" in m.content.lower()]
        
        results = benchmark(search_messages)
        assert len(results) >= 20

    def test_edit_message_performance(self, benchmark, modules, test_dm):
        """Test message editing performance."""
        dm, user1, user2 = test_dm
        
        msg = modules.messaging.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="Original content"
        )
        
        def edit_message():
            return modules.messaging.edit_message(
                user_id=user1.id,
                message_id=msg.id,
                content="Edited content"
            )
        
        result = benchmark(edit_message)
        assert result.content == "Edited content"

    def test_conversation_list_performance(self, benchmark, modules, user_pool):
        """Test retrieving user's conversation list."""
        user = user_pool.get_user()
        
        for i in range(20):
            other_user = user_pool.get_user()
            dm = modules.messaging.create_dm(user.id, other_user.id)
            modules.messaging.send_message(
                user_id=user.id,
                conversation_id=dm.id,
                content=f"Message in conversation {i}"
            )
        
        def get_conversations():
            return modules.messaging.get_conversations(user.id)
        
        conversations = benchmark(get_conversations)
        assert len(conversations) >= 20


class TestMessagingMemory:
    """Test messaging memory usage and leaks."""

    def test_send_message_memory_leak(self, modules, test_dm, memory_tracker):
        """Check for memory leaks during repeated message sends."""
        dm, user1, user2 = test_dm
        
        initial_memory = memory_tracker.snapshot()
        
        for i in range(500):
            modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content=f"Memory test message {i}"
            )
            
            if i % 100 == 0:
                memory_tracker.snapshot()
        
        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory
        
        assert memory_increase < 50, f"Memory increased by {memory_increase}MB, potential leak"

    def test_get_messages_memory_leak(self, modules, test_dm, memory_tracker):
        """Check for memory leaks during repeated message retrievals."""
        dm, user1, user2 = test_dm
        
        for i in range(100):
            modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content=f"Test message {i}"
            )
        
        initial_memory = memory_tracker.snapshot()
        
        for i in range(200):
            modules.messaging.get_messages(
                user_id=user1.id,
                conversation_id=dm.id,
                limit=50
            )
            
            if i % 40 == 0:
                memory_tracker.snapshot()
        
        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory
        
        assert memory_increase < 20, f"Memory increased by {memory_increase}MB, potential leak"

    def test_large_message_memory(self, modules, test_dm, memory_tracker):
        """Test memory usage with large messages."""
        dm, user1, user2 = test_dm
        
        initial_memory = memory_tracker.snapshot()
        
        large_content = "x" * 3000
        
        for i in range(100):
            modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content=large_content
            )
        
        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory
        
        expected_max = 50
        assert memory_increase < expected_max, f"Memory increased by {memory_increase}MB (expected < {expected_max}MB)"


class TestMessagingDegradation:
    """Test messaging performance under sustained load."""

    def test_send_performance_degradation(self, modules, test_dm):
        """Ensure send performance doesn't degrade over time."""
        dm, user1, user2 = test_dm
        
        times = []
        
        for i in range(200):
            start = datetime.now()
            modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content=f"Degradation test {i}"
            )
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)
        
        first_batch_avg = sum(times[:20]) / 20
        last_batch_avg = sum(times[-20:]) / 20
        
        degradation = (last_batch_avg - first_batch_avg) / first_batch_avg
        
        assert degradation < 1.0, f"Performance degraded by {degradation*100:.1f}%"

    def test_large_conversation_performance(self, modules, test_dm):
        """Test performance with large conversation history."""
        dm, user1, user2 = test_dm
        
        for i in range(1000):
            modules.messaging.send_message(
                user_id=user1.id if i % 2 == 0 else user2.id,
                conversation_id=dm.id,
                content=f"Message {i}"
            )
        
        times = []
        for _ in range(20):
            start = datetime.now()
            modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content="Test after 1000 messages"
            )
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)
        
        avg_time = sum(times) / len(times)
        assert avg_time < 0.2, f"Send too slow with large history: {avg_time}s"

    def test_concurrent_send_scaling(self, modules, test_dm):
        """Test how message sending scales with concurrency."""
        dm, user1, user2 = test_dm
        
        def send_batch(count, workers):
            start = datetime.now()
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(
                        modules.messaging.send_message,
                        user1.id,
                        dm.id,
                        f"Scale test {i}"
                    )
                    for i in range(count)
                ]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]
            elapsed = (datetime.now() - start).total_seconds()
            return elapsed, len(results)
        
        time_1, count_1 = send_batch(20, 1)
        time_5, count_5 = send_batch(20, 5)
        time_10, count_10 = send_batch(20, 10)
        
        assert count_1 == 20
        assert count_5 == 20
        assert count_10 == 20
        
        assert time_5 < time_1, "Parallel should be faster than sequential"
        assert time_10 < time_1, "More parallelism should improve performance"
