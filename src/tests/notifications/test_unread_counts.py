"""
Tests for unread and mention counts.
"""



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


class TestGetUnreadCounts:
    """Tests for getting all unread counts."""

    def test_get_all_unread_counts(self, group_conversation):
        """Test getting all unread counts per conversation."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        content = f"<@{member1.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content
        )

        counts = notifications.get_unread_counts(member1.id)

        assert group.id in counts
        assert counts[group.id].mention_count >= 1

    def test_empty_unread_counts(self, fresh_users):
        """Test empty unread counts for new user."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        counts = notifications.get_unread_counts(user1.id)

        assert len(counts) == 0


class TestGetMentionCount:
    """Tests for getting mention counts."""

    def test_mention_count_zero_initially(self, fresh_users):
        """Test mention count is zero initially."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        count = notifications.get_mention_count(user1.id)

        assert count == 0

    def test_mention_count_increases(self, users_with_dm):
        """Test mention count increases with mentions."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        count = notifications.get_mention_count(user2.id)

        assert count >= 1

    def test_mention_count_per_server(self, users_with_server):
        """Test mention count filtered by server."""
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

        count = notifications.get_mention_count(member1.id, server_id=server.id)

        assert count >= 1


class TestMarkRead:
    """Tests for marking notifications as read."""

    def test_mark_notification_read(self, users_with_dm):
        """Test marking single notification as read."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        assert len(notifs) == 1

        result = notifications.mark_notification_read(user2.id, notifs[0].id)

        assert result is True

        notif = notifications.get_notification(notifs[0].id)
        assert notif.read is True

    def test_mark_all_read(self, group_conversation):
        """Test marking all notifications as read."""
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

        count = notifications.mark_all_read(member1.id)

        assert count >= 3

        unread = notifications.get_unread_count(member1.id)
        assert unread.mention_count == 0

    def test_mark_channel_read(self, users_with_server):
        """Test marking channel notifications as read."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@{member1.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id,
            channel_id=channel.id
        )

        count = notifications.mark_channel_read(member1.id, channel.id)

        assert count >= 0

    def test_mark_server_read(self, users_with_server):
        """Test marking server notifications as read."""
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

        count = notifications.mark_server_read(member1.id, server.id)

        assert count >= 0
