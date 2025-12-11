"""
Tests for user notification settings.
"""

from src.core.notifications import NotificationLevel


class TestGetNotificationSettings:
    """Tests for getting notification settings."""

    def test_get_default_settings(self, fresh_users):
        """Test getting default settings for user without custom settings."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        settings = notifications.get_notification_settings(user1.id)

        assert settings.user_id == user1.id
        assert settings.server_id is None
        assert settings.level == NotificationLevel.ALL_MESSAGES
        assert settings.dm_notifications is True
        assert settings.suppress_everyone is False
        assert settings.suppress_roles is False
        assert settings.mobile_push is True

    def test_get_server_specific_settings(self, users_with_server):
        """Test getting server-specific settings."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        notifications.update_notification_settings(
            user_id=member1.id,
            server_id=server.id,
            level=NotificationLevel.ONLY_MENTIONS
        )

        settings = notifications.get_notification_settings(member1.id, server.id)

        assert settings.server_id == server.id
        assert settings.level == NotificationLevel.ONLY_MENTIONS


class TestUpdateNotificationSettings:
    """Tests for updating notification settings."""

    def test_update_global_settings(self, fresh_users):
        """Test updating global notification settings."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        settings = notifications.update_notification_settings(
            user_id=user1.id,
            level=NotificationLevel.ONLY_MENTIONS,
            dm_notifications=False,
            suppress_everyone=True,
            mobile_push=False
        )

        assert settings.level == NotificationLevel.ONLY_MENTIONS
        assert settings.dm_notifications is False
        assert settings.suppress_everyone is True
        assert settings.mobile_push is False

    def test_update_server_settings(self, users_with_server):
        """Test updating server-specific settings."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        settings = notifications.update_notification_settings(
            user_id=member1.id,
            server_id=server.id,
            level=NotificationLevel.NOTHING,
            suppress_roles=True
        )

        assert settings.server_id == server.id
        assert settings.level == NotificationLevel.NOTHING
        assert settings.suppress_roles is True

    def test_update_settings_twice(self, fresh_users):
        """Test updating settings multiple times."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        notifications.update_notification_settings(
            user_id=user1.id,
            level=NotificationLevel.ONLY_MENTIONS
        )

        settings = notifications.update_notification_settings(
            user_id=user1.id,
            level=NotificationLevel.NOTHING
        )

        assert settings.level == NotificationLevel.NOTHING


class TestSuppressEveryone:
    """Tests for suppress @everyone setting."""

    def test_suppress_everyone_blocks_notification(self, users_with_server):
        """Test suppress_everyone prevents @everyone notifications."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        notifications.update_notification_settings(
            user_id=member1.id,
            server_id=server.id,
            suppress_everyone=True
        )

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = "@everyone check this"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id not in notified_users
        assert member2.id in notified_users

    def test_suppress_everyone_allows_direct_mention(self, users_with_server):
        """Test suppress_everyone still allows direct @user mentions."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        notifications.update_notification_settings(
            user_id=member1.id,
            server_id=server.id,
            suppress_everyone=True
        )

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@{member1.id}> check this"
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


class TestSuppressRoles:
    """Tests for suppress @role setting."""

    def test_suppress_roles_blocks_notification(self, users_with_role):
        """Test suppress_roles prevents @role notifications."""
        owner, member1, member2, server, channel, role, servers, messaging, notifications = users_with_role

        notifications.update_notification_settings(
            user_id=member1.id,
            server_id=server.id,
            suppress_roles=True
        )

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@&{role.id}> check this"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id not in notified_users


class TestNotificationLevelNothing:
    """Tests for notification level NOTHING."""

    def test_nothing_level_blocks_all(self, users_with_server):
        """Test NOTHING level blocks all notifications."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        notifications.update_notification_settings(
            user_id=member1.id,
            server_id=server.id,
            level=NotificationLevel.NOTHING
        )

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@{member1.id}> @everyone check this"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id not in notified_users
        assert member2.id in notified_users
