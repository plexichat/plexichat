"""
Tests for unread and mention counts.
"""

import pytest
from src.core.notifications import MentionType


class TestGetUnreadCount:
    """Tests for getting unread counts."""

    def test_initial_unread_count_zero(self, fresh_users):
        """Test initial unread count is zero."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        unread = notifications.get_unread_count(user1.id)

        assert unread.total_unread == 0
        assert unread.mention_count == 0

    def test_unread_count_after_mention(self, users_with_dm):
        """Test unread count increases after mention."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"Hey <@{user2.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        unread = notifications.get_unread_count(user2.id)

        assert unread.mention_count >= 1

    def test_unread_count_per_server(self, users_with_server):
        """Test unread count filtered by server."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@{member1.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        unread = notifications.get_unread_count(member1.id, server_id=server.id)

        assert unread.server_id == server.id
        assert unread.mention_count >= 1
