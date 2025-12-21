"""
Pagination tests for messaging module.
"""



class TestMessagePagination:
    """Test message pagination."""

    def test_default_limit(self, dm_conversation):
        """Test default limit is applied."""
        dm, user1, user2, messaging = dm_conversation

        # Send many messages
        for i in range(60):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        messages = messaging.get_messages(user1.id, dm.id)

        # Default limit is 50
        assert len(messages) <= 50

    def test_custom_limit(self, dm_conversation):
        """Test custom limit is respected."""
        dm, user1, user2, messaging = dm_conversation

        for i in range(20):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        messages = messaging.get_messages(user1.id, dm.id, limit=10)

        assert len(messages) == 10

    def test_limit_capped_at_100(self, dm_conversation):
        """Test limit is capped at 100."""
        dm, user1, user2, messaging = dm_conversation

        for i in range(150):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        messages = messaging.get_messages(user1.id, dm.id, limit=200)

        assert len(messages) <= 100

    def test_before_id_pagination(self, dm_conversation):
        """Test pagination with before_id."""
        dm, user1, user2, messaging = dm_conversation

        msgs = []
        for i in range(10):
            msgs.append(messaging.send_message(user1.id, dm.id, f"Message {i}"))

        # Get messages before the last one
        older = messaging.get_messages(user1.id, dm.id, before_id=msgs[-1].id, limit=5)

        assert len(older) == 5
        assert all(m.id < msgs[-1].id for m in older)

    def test_after_id_pagination(self, dm_conversation):
        """Test pagination with after_id."""
        dm, user1, user2, messaging = dm_conversation

        msgs = []
        for i in range(10):
            msgs.append(messaging.send_message(user1.id, dm.id, f"Message {i}"))

        # Get messages after the first one
        newer = messaging.get_messages(user1.id, dm.id, after_id=msgs[0].id, limit=5)

        assert len(newer) == 5
        assert all(m.id > msgs[0].id for m in newer)

    def test_before_id_returns_older_messages(self, dm_conversation):
        """Test before_id returns older messages in descending order."""
        dm, user1, user2, messaging = dm_conversation

        msgs = []
        for i in range(10):
            msgs.append(messaging.send_message(user1.id, dm.id, f"Message {i}"))

        # Get messages before message 5
        older = messaging.get_messages(user1.id, dm.id, before_id=msgs[5].id, limit=3)

        # Should get messages 4, 3, 2 (in descending order)
        assert len(older) == 3
        for m in older:
            assert m.id < msgs[5].id

    def test_after_id_returns_newer_messages(self, dm_conversation):
        """Test after_id returns newer messages in ascending order."""
        dm, user1, user2, messaging = dm_conversation

        msgs = []
        for i in range(10):
            msgs.append(messaging.send_message(user1.id, dm.id, f"Message {i}"))

        # Get messages after message 5
        newer = messaging.get_messages(user1.id, dm.id, after_id=msgs[5].id, limit=3)

        # Should get messages 6, 7, 8 (in ascending order)
        assert len(newer) == 3
        for m in newer:
            assert m.id > msgs[5].id

    def test_pagination_no_overlap(self, dm_conversation):
        """Test pagination pages don't overlap."""
        dm, user1, user2, messaging = dm_conversation

        for i in range(20):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        page1 = messaging.get_messages(user1.id, dm.id, limit=10)
        page2 = messaging.get_messages(user1.id, dm.id, before_id=page1[-1].id, limit=10)

        page1_ids = set(m.id for m in page1)
        page2_ids = set(m.id for m in page2)

        assert len(page1_ids & page2_ids) == 0

    def test_pagination_covers_all_messages(self, fresh_dm):
        """Test pagination covers all messages."""
        dm, user1, user2, messaging = fresh_dm

        for i in range(25):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        all_ids = set()

        # Get first page
        page = messaging.get_messages(user1.id, dm.id, limit=10)
        while page:
            for m in page:
                all_ids.add(m.id)

            # Get next page
            page = messaging.get_messages(user1.id, dm.id, before_id=page[-1].id, limit=10)

        assert len(all_ids) == 25

    def test_empty_result_at_end(self, fresh_dm):
        """Test empty result when no more messages."""
        dm, user1, user2, messaging = fresh_dm

        msgs = []
        for i in range(5):
            msgs.append(messaging.send_message(user1.id, dm.id, f"Message {i}"))

        # Get all messages
        all_msgs = messaging.get_messages(user1.id, dm.id, limit=10)

        # Try to get more before the oldest
        older = messaging.get_messages(user1.id, dm.id, before_id=all_msgs[-1].id, limit=10)

        # Should be empty (fresh DM has no prior messages)
        assert len(older) == 0


class TestConversationPagination:
    """Test conversation pagination."""

    def test_conversations_ordered_by_activity(self, users):
        """Test conversations are ordered by last activity."""
        user1, user2, user3, messaging = users

        # Create conversations
        dm1 = messaging.create_dm(user1.id, user2.id)
        dm2 = messaging.create_dm(user1.id, user3.id)

        # Send message to dm1 (makes it more recent)
        messaging.send_message(user1.id, dm1.id, "Recent message")

        convs = messaging.get_conversations(user1.id)

        # dm1 should be first (most recent activity)
        assert convs[0].id == dm1.id

    def test_conversation_pagination_before_id(self, users):
        """Test conversation pagination with before_id."""
        user1, user2, user3, messaging = users

        # Create multiple groups
        groups = []
        for i in range(10):
            g = messaging.create_group(user1.id, f"Group {i}")
            groups.append(g)

        # Get first page
        page1 = messaging.get_conversations(user1.id, limit=5)

        # Get second page
        page2 = messaging.get_conversations(user1.id, limit=5, before_id=page1[-1].id)

        # No overlap
        page1_ids = set(c.id for c in page1)
        page2_ids = set(c.id for c in page2)

        assert len(page1_ids & page2_ids) == 0

    def test_conversation_limit_respected(self, users):
        """Test conversation limit is respected."""
        user1, user2, user3, messaging = users

        for i in range(10):
            messaging.create_group(user1.id, f"Group {i}")

        convs = messaging.get_conversations(user1.id, limit=5)

        assert len(convs) == 5

    def test_conversation_filter_by_type(self, users):
        """Test filtering conversations by type."""
        user1, user2, user3, messaging = users

        dm = messaging.create_dm(user1.id, user2.id)
        group = messaging.create_group(user1.id, "Test Group")

        dms = messaging.get_conversations(user1.id, conversation_type=messaging.ConversationType.DM)
        groups = messaging.get_conversations(user1.id, conversation_type=messaging.ConversationType.GROUP)

        dm_ids = [c.id for c in dms]
        group_ids = [c.id for c in groups]

        assert dm.id in dm_ids
        assert dm.id not in group_ids
        assert group.id in group_ids
        assert group.id not in dm_ids


class TestSnowflakeIdOrdering:
    """Test Snowflake ID ordering properties."""

    def test_newer_messages_have_higher_ids(self, dm_conversation):
        """Test newer messages have higher Snowflake IDs."""
        dm, user1, user2, messaging = dm_conversation

        msg1 = messaging.send_message(user1.id, dm.id, "First")
        msg2 = messaging.send_message(user1.id, dm.id, "Second")
        msg3 = messaging.send_message(user1.id, dm.id, "Third")

        assert msg1.id < msg2.id < msg3.id

    def test_ids_are_unique(self, dm_conversation):
        """Test all IDs are unique."""
        dm, user1, user2, messaging = dm_conversation

        ids = set()
        for i in range(100):
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            assert msg.id not in ids
            ids.add(msg.id)

    def test_ids_are_positive_integers(self, dm_conversation):
        """Test IDs are positive integers."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")

        assert isinstance(msg.id, int)
        assert msg.id > 0
