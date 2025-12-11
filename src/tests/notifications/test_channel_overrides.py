"""
Tests for per-channel notification overrides.
"""

import time
from src.core.notifications import NotificationLevel


class TestGetChannelOverride:
    """Tests for getting channel overrides."""

    def test_get_nonexistent_override(self, users_with_server):
        """Test getting override that doesn't exist returns None."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        override = notifications.get_channel_override(member1.id, channel.id)

        assert override is None

    def test_get_existing_override(self, users_with_server):
        """Test getting existing override."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.MUTED
        )

        override = notifications.get_channel_override(member1.id, channel.id)

        assert override is not None
        assert override.user_id == member1.id
        assert override.channel_id == channel.id
        assert override.level == NotificationLevel.MUTED


class TestSetChannelOverride:
    """Tests for setting channel overrides."""

    def test_set_muted_override(self, users_with_server):
        """Test setting muted override."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        override = notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.MUTED
        )

        assert override.level == NotificationLevel.MUTED

    def test_set_mentions_only_override(self, users_with_server):
        """Test setting mentions-only override."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        override = notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.ONLY_MENTIONS
        )

        assert override.level == NotificationLevel.ONLY_MENTIONS

    def test_set_override_with_mute_expiration(self, users_with_server):
        """Test setting override with mute expiration."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        future_time = int(time.time() * 1000) + 3600000

        override = notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.MUTED,
            muted_until=future_time
        )

        assert override.muted_until == future_time

    def test_update_existing_override(self, users_with_server):
        """Test updating existing override."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.MUTED
        )

        override = notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.ALL_MESSAGES
        )

        assert override.level == NotificationLevel.ALL_MESSAGES


class TestDeleteChannelOverride:
    """Tests for deleting channel overrides."""

    def test_delete_existing_override(self, users_with_server):
        """Test deleting existing override."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.MUTED
        )

        result = notifications.delete_channel_override(member1.id, channel.id)

        assert result is True

        override = notifications.get_channel_override(member1.id, channel.id)
        assert override is None

    def test_delete_nonexistent_override(self, users_with_server):
        """Test deleting nonexistent override returns False."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        result = notifications.delete_channel_override(member1.id, channel.id)

        assert result is False


class TestChannelOverrideEffect:
    """Tests for channel override effects on notifications."""

    def test_muted_channel_blocks_notifications(self, users_with_server):
        """Test muted channel blocks all notifications."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.MUTED
        )

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@{member1.id}> check this"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id,
            channel_id=channel.id
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id not in notified_users

    def test_nothing_level_blocks_notifications(self, users_with_server):
        """Test NOTHING level blocks all notifications."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.NOTHING
        )

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

        notified_users = {n.user_id for n in notifs}
        assert member1.id not in notified_users

    def test_expired_mute_allows_notifications(self, users_with_server):
        """Test expired mute allows notifications."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        past_time = int(time.time() * 1000) - 3600000

        notifications.set_channel_override(
            user_id=member1.id,
            channel_id=channel.id,
            level=NotificationLevel.MUTED,
            muted_until=past_time
        )

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

        notified_users = {n.user_id for n in notifs}
        assert member1.id in notified_users
