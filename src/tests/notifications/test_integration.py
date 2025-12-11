"""
Tests for integration with other modules.
"""

from src.core.notifications import MentionType


class TestMessagingIntegration:
    """Tests for integration with messaging module."""

    def test_notification_includes_message_id(self, users_with_dm):
        """Test notification includes correct message ID."""
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
        assert notifs[0].message_id == msg.id

    def test_notification_includes_conversation_id(self, users_with_dm):
        """Test notification includes correct conversation ID."""
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
        assert notifs[0].conversation_id == dm.id

    def test_notification_in_group_conversation(self, group_conversation):
        """Test notifications work in group conversations."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        content = f"<@{member1.id}> and <@{member2.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content
        )

        assert len(notifs) == 2
        notified_users = {n.user_id for n in notifs}
        assert member1.id in notified_users
        assert member2.id in notified_users


class TestServersIntegration:
    """Tests for integration with servers module."""

    def test_notification_includes_server_id(self, users_with_server):
        """Test notification includes server ID when provided."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@{member1.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        assert len(notifs) == 1
        assert notifs[0].server_id == server.id

    def test_notification_includes_channel_id(self, users_with_server):
        """Test notification includes channel ID when provided."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@{member1.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id,
            channel_id=channel.id
        )

        assert len(notifs) == 1
        assert notifs[0].channel_id == channel.id

    def test_role_mention_notifies_role_members(self, users_with_role):
        """Test role mention notifies all role members."""
        owner, member1, member2, server, channel, role, servers, messaging, notifications = users_with_role

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@&{role.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id in notified_users

    def test_everyone_notifies_server_members(self, users_with_server):
        """Test @everyone notifies all server members."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = "@everyone"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id in notified_users
        assert member2.id in notified_users


class TestRelationshipsIntegration:
    """Tests for integration with relationships module."""

    def test_blocked_user_not_notified(self, group_conversation):
        """Test blocked user doesn't receive notification."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        relationships.block_user(member1.id, owner.id)

        content = f"<@{member1.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id not in notified_users

    def test_user_who_blocked_sender_not_notified(self, db_and_modules):
        """Test user who blocked sender doesn't receive notification."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"block1_{unique_id}",
            email=f"block1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"block2_{unique_id}",
            email=f"block2_{unique_id}@example.com",
            password="TestPass123!"
        )
        user3 = auth.register(
            username=f"block3_{unique_id}",
            email=f"block3_{unique_id}@example.com",
            password="TestPass123!"
        )

        group = messaging.create_group(user1.id, f"Block Test {unique_id}", [user2.id, user3.id])

        relationships.block_user(user2.id, user1.id)

        content = f"<@{user2.id}> <@{user3.id}>"
        msg = messaging.send_message(user1.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content
        )

        notified_users = {n.user_id for n in notifs}
        assert user2.id not in notified_users
        assert user3.id in notified_users


class TestHighlightMentions:
    """Tests for highlight mentions functionality."""

    def test_highlight_user_mention(self, users_with_dm):
        """Test highlighting user mention."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"Hey <@{user2.id}> check this"
        positions = notifications.highlight_mentions(content, user2.id)

        assert len(positions) == 1
        assert positions[0].mention_type == MentionType.USER
        assert positions[0].is_self is True

    def test_highlight_other_user_mention(self, users_with_dm):
        """Test highlighting mention of other user."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"Hey <@{user2.id}> check this"
        positions = notifications.highlight_mentions(content, user1.id)

        assert len(positions) == 1
        assert positions[0].is_self is False

    def test_highlight_everyone_mention(self, users_with_server):
        """Test highlighting @everyone mention."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        content = "@everyone check this"
        positions = notifications.highlight_mentions(content, member1.id)

        assert len(positions) == 1
        assert positions[0].mention_type == MentionType.EVERYONE
        assert positions[0].is_self is True

    def test_highlight_multiple_mentions(self, group_conversation):
        """Test highlighting multiple mentions."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        content = f"<@{member1.id}> and <@{member2.id}> @everyone"
        positions = notifications.highlight_mentions(content, member1.id)

        assert len(positions) == 3
        self_mentions = [p for p in positions if p.is_self]
        assert len(self_mentions) == 2


class TestPushPayload:
    """Tests for push notification payload preparation."""

    def test_prepare_push_payload(self, users_with_dm):
        """Test preparing push notification payload."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}> check this"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        assert len(notifs) == 1

        payload = notifications.prepare_push_payload(notifs[0])

        assert payload.user_id == user2.id
        assert "mentioned" in payload.title.lower()
        assert payload.data["notification_id"] == notifs[0].id
        assert payload.data["message_id"] == msg.id

    def test_push_payload_badge_count(self, group_conversation):
        """Test push payload includes badge count."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        notifs = []
        for i in range(3):
            content = f"<@{member1.id}> message {i}"
            msg = messaging.send_message(owner.id, group.id, content)
            notifs = notifications.create_notifications_for_message(
                sender_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content
            )

        if notifs:
            payload = notifications.prepare_push_payload(notifs[0])
            assert payload.badge_count >= 1
