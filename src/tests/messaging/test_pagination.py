"""
Pagination and message ordering tests.

Tests cursor-based pagination, ordering, limits,
and edge cases in message retrieval.
"""

import pytest
import asyncio


class TestBasicPagination:
    """Tests for basic pagination."""

    def test_get_messages_with_limit(self, dm_conversation):
        """Test getting messages with limit."""
        dm, user1, user2, messaging = dm_conversation

        # Create 20 messages
        for i in range(20):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        messages = messaging.get_messages(user1.id, dm.id, limit=10)
        assert len(messages) == 10

    def test_get_messages_default_limit(self, dm_conversation):
        """Test default limit is applied."""
        dm, user1, user2, messaging = dm_conversation

        # Create 100 messages
        for i in range(100):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        messages = messaging.get_messages(user1.id, dm.id)
        assert len(messages) <= 50  # Default limit

    def test_get_messages_max_limit_cap(self, dm_conversation):
        """Test that limit is capped at maximum."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        for i in range(150):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        # Request more than max
        messages = messaging.get_messages(user1.id, dm.id, limit=200)
        assert len(messages) <= 100  # Capped at 100


class TestCursorPagination:
    """Tests for cursor-based pagination."""

    def test_pagination_with_before_id(self, dm_conversation):
        """Test pagination using before_id cursor."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        msgs = []
        for i in range(10):
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            msgs.append(msg)

        # Get messages before 5th message
        page = messaging.get_messages(user1.id, dm.id, limit=3, before_id=msgs[5].id)

        assert len(page) == 3
        assert all(m.id < msgs[5].id for m in page)

    def test_pagination_with_after_id(self, dm_conversation):
        """Test pagination using after_id cursor."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        msgs = []
        for i in range(10):
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            msgs.append(msg)

        # Get messages after 3rd message
        page = messaging.get_messages(user1.id, dm.id, limit=3, after_id=msgs[2].id)

        assert len(page) == 3
        assert all(m.id > msgs[2].id for m in page)

    def test_pagination_before_oldest(self, dm_conversation):
        """Test pagination before oldest message returns empty."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "First")

        # Try to get messages before first
        page = messaging.get_messages(user1.id, dm.id, before_id=msg.id)
        assert len(page) == 0

    def test_pagination_after_newest(self, dm_conversation):
        """Test pagination after newest message returns empty."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Last")

        # Try to get messages after last
        page = messaging.get_messages(user1.id, dm.id, after_id=msg.id)
        assert len(page) == 0

    def test_pagination_chain(self, dm_conversation):
        """Test chaining pagination requests."""
        dm, user1, user2, messaging = dm_conversation

        # Create 15 messages
        msgs = []
        for i in range(15):
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            msgs.append(msg)

        # Get first page
        page1 = messaging.get_messages(user1.id, dm.id, limit=5)
        assert len(page1) == 5

        # Get second page
        page2 = messaging.get_messages(user1.id, dm.id, limit=5, before_id=page1[-1].id)
        assert len(page2) == 5

        # Get third page
        page3 = messaging.get_messages(user1.id, dm.id, limit=5, before_id=page2[-1].id)
        assert len(page3) == 5

        # Verify no overlap
        all_ids = [m.id for m in page1 + page2 + page3]
        assert len(all_ids) == len(set(all_ids))


class TestMessageOrdering:
    """Tests for message ordering."""

    def test_messages_ordered_by_id_desc(self, dm_conversation):
        """Test that messages are ordered by ID descending."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        for i in range(10):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        messages = messaging.get_messages(user1.id, dm.id, limit=10)

        # Should be in descending order (newest first)
        for i in range(len(messages) - 1):
            assert messages[i].id > messages[i + 1].id

    def test_messages_with_after_ordered_asc(self, dm_conversation):
        """Test that messages with after_id are ordered ascending."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        msgs = []
        for i in range(10):
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            msgs.append(msg)

        # Get messages after 2nd
        page = messaging.get_messages(user1.id, dm.id, after_id=msgs[1].id, limit=5)

        # Should be in ascending order when using after_id
        for i in range(len(page) - 1):
            assert page[i].id < page[i + 1].id

    def test_message_id_uniqueness(self, dm_conversation):
        """Test that message IDs are unique."""
        dm, user1, user2, messaging = dm_conversation

        # Create many messages
        msgs = []
        for i in range(100):
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            msgs.append(msg)

        # Check all IDs are unique
        ids = [m.id for m in msgs]
        assert len(ids) == len(set(ids))


class TestConversationPagination:
    """Tests for conversation list pagination."""

    def test_get_conversations_with_limit(self, user_pool, modules):
        """Test getting conversations with limit."""
        user = user_pool.get_user()

        # Create multiple conversations
        for i in range(10):
            other = user_pool.get_user()
            modules.messaging.create_dm(user.id, other.id)

        convs = modules.messaging.get_conversations(user.id, limit=5)
        assert len(convs) <= 5

    def test_get_conversations_pagination(self, user_pool, modules):
        """Test conversation pagination."""
        user = user_pool.get_user()

        # Create conversations
        for i in range(10):
            other = user_pool.get_user()
            modules.messaging.create_dm(user.id, other.id)

        # First page
        page1 = modules.messaging.get_conversations(user.id, limit=3)
        assert len(page1) == 3

        # Second page
        page2 = modules.messaging.get_conversations(
            user.id, limit=3, before_id=page1[-1].id
        )

        # Verify no overlap
        page1_ids = {c.id for c in page1}
        page2_ids = {c.id for c in page2}
        assert page1_ids.isdisjoint(page2_ids)

    def test_conversations_ordered_by_activity(self, user_pool, modules):
        """Test that conversations are ordered by last activity."""
        user = user_pool.get_user()

        # Create conversations
        convs = []
        for i in range(5):
            other = user_pool.get_user()
            conv = modules.messaging.create_dm(user.id, other.id)
            convs.append(conv)

        # Send message in middle conversation
        import time

        time.sleep(0.01)
        modules.messaging.send_message(user.id, convs[2].id, "Update")

        # Get conversations
        result = modules.messaging.get_conversations(user.id, limit=10)

        # Most recently updated should be first
        assert result[0].id == convs[2].id


class TestPaginationEdgeCases:
    """Tests for pagination edge cases."""

    def test_pagination_with_zero_limit(self, dm_conversation):
        """Test pagination with zero limit."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Test")

        # Zero limit should return nothing or minimum
        messages = messaging.get_messages(user1.id, dm.id, limit=0)
        assert len(messages) == 0

    def test_pagination_with_negative_limit(self, dm_conversation):
        """Test pagination with negative limit."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Test")

        # Should handle gracefully
        messages = messaging.get_messages(user1.id, dm.id, limit=-1)
        # Implementation may treat as 0 or default
        assert messages is not None

    def test_pagination_nonexistent_cursor(self, dm_conversation):
        """Test pagination with non-existent cursor."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        for i in range(5):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        # Use non-existent ID as cursor
        messages = messaging.get_messages(user1.id, dm.id, before_id=999999999)
        # Should return empty or all messages
        assert messages is not None

    def test_pagination_deleted_messages_excluded(self, dm_conversation):
        """Test that deleted messages are excluded from pagination."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        msgs = []
        for i in range(10):
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            msgs.append(msg)

        # Delete some messages
        for i in [2, 4, 6]:
            messaging.delete_message(user1.id, msgs[i].id)

        # Get all messages
        messages = messaging.get_messages(user1.id, dm.id, limit=20)

        # Should only have 7 messages
        assert len(messages) == 7
        # Deleted IDs should not be present
        msg_ids = [m.id for m in messages]
        assert msgs[2].id not in msg_ids
        assert msgs[4].id not in msg_ids
        assert msgs[6].id not in msg_ids


@pytest.mark.asyncio
class TestPaginationConcurrency:
    """Tests for pagination under concurrent access."""

    async def test_pagination_during_concurrent_sends(self, dm_conversation):
        """Test pagination while messages are being sent."""
        dm, user1, user2, messaging = dm_conversation

        # Create initial messages
        for i in range(10):
            await asyncio.to_thread(
                messaging.send_message, user1.id, dm.id, f"Initial {i}"
            )

        # Start pagination
        async def paginate():
            return await asyncio.to_thread(
                messaging.get_messages, user1.id, dm.id, limit=5
            )

        # Send more messages concurrently
        async def send_more():
            for i in range(5):
                await asyncio.to_thread(
                    messaging.send_message, user1.id, dm.id, f"Concurrent {i}"
                )

        results = await asyncio.gather(paginate(), send_more())
        messages = results[0]

        # Should get consistent results
        assert len(messages) == 5

    async def test_concurrent_pagination_requests(self, dm_conversation):
        """Test multiple concurrent pagination requests."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        for i in range(50):
            await asyncio.to_thread(
                messaging.send_message, user1.id, dm.id, f"Message {i}"
            )

        # Make concurrent pagination requests
        tasks = [
            asyncio.to_thread(messaging.get_messages, user1.id, dm.id, limit=10)
            for _ in range(10)
        ]

        results = await asyncio.gather(*tasks)

        # All should succeed
        assert all(len(msgs) == 10 for msgs in results)


class TestComplexPaginationScenarios:
    """Tests for complex pagination scenarios."""

    def test_pagination_across_multiple_senders(self, dm_conversation):
        """Test pagination with messages from multiple senders."""
        dm, user1, user2, messaging = dm_conversation

        # Alternate senders
        for i in range(20):
            sender = user1 if i % 2 == 0 else user2
            messaging.send_message(sender.id, dm.id, f"Message {i}")

        # Paginate
        page1 = messaging.get_messages(user1.id, dm.id, limit=5)
        page2 = messaging.get_messages(user1.id, dm.id, limit=5, before_id=page1[-1].id)

        # Should include messages from both senders
        assert len(page1) == 5
        assert len(page2) == 5

    def test_pagination_with_system_messages(self, group_conversation):
        """Test pagination includes system messages."""
        group, owner, member1, member2, messaging = group_conversation

        # Send regular messages and trigger system messages
        messaging.send_message(owner.id, group.id, "Test 1")
        messaging.send_system_message(group.id, "System", "test_event")
        messaging.send_message(owner.id, group.id, "Test 2")

        # Paginate
        messages = messaging.get_messages(owner.id, group.id, limit=10)

        # Should include both regular and system messages
        assert len(messages) >= 3

    def test_pagination_preserves_reply_chain(self, dm_conversation):
        """Test that pagination preserves reply relationships."""
        dm, user1, user2, messaging = dm_conversation

        # Create reply chain
        msg1 = messaging.send_message(user1.id, dm.id, "Root")
        msg2 = messaging.send_message(user2.id, dm.id, "Reply 1", reply_to_id=msg1.id)
        msg3 = messaging.send_message(user1.id, dm.id, "Reply 2", reply_to_id=msg2.id)

        # Get messages
        messages = messaging.get_messages(user1.id, dm.id, limit=10)

        # Find messages in result
        result_map = {m.id: m for m in messages}

        assert msg2.id in result_map
        assert result_map[msg2.id].reply_to_id == msg1.id
        assert result_map[msg3.id].reply_to_id == msg2.id
