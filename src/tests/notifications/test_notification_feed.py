"""
Tests for notification feed functionality.
"""

import pytest
from src.core.notifications import MentionType


class TestGetNotificationFeed:
    """Tests for getting notification feed."""

    def test_empty_feed(self, fresh_users):
        """Test empty feed for new user."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        feed = notifications.get_notification_feed(user1.id)

        assert len(feed.notifications) == 0
        assert feed.total_count == 0
        assert feed.unread_count == 0
        assert feed.has_more is False

    def test_feed_with_notifications(self, users_with_dm):
        """Test feed with notifications."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}> check this"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        feed = notifications.get_notification_feed(user2.id)

        assert len(feed.notifications) >= 1
        assert feed.total_count >= 1
        assert feed.unread_count >= 1

    def test_feed_order(self, group_conversation):
        """Test feed is ordered by most recent first."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        for i in range(3):
            content = f"<@{member1.id}> message {i}"
            msg = messaging.send_message(owner.id, group.id, content)
            notifications.create_notifications_for_message(
                sender_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content
            )

        feed = notifications.get_notification_feed(member1.id)

        assert len(feed.notifications) >= 3
        for i in range(len(feed.notifications) - 1):
            assert feed.notifications[i].created_at >= feed.notifications[i + 1].created_at
