"""
Tests for per-channel notification overrides.
"""

import time
from unittest.mock import patch
from src.core.notifications import NotificationLevel
from src.utils import encryption


class TestGetChannelOverride:
    """Tests for getting channel overrides."""

    def test_get_nonexistent_override(self, notification_manager):
        """Test getting override that doesn't exist returns None."""
        override = notification_manager.get_channel_override(123, 456)

        assert override is None

    def test_get_existing_override(self, notification_manager):
        """Test getting existing override."""
        notification_manager.set_channel_override(
            user_id=123, channel_id=456, level=NotificationLevel.MUTED
        )

        retrieved = notification_manager.get_channel_override(123, 456)

        assert retrieved is not None
        assert retrieved.user_id == 123
        assert retrieved.channel_id == 456
        assert retrieved.level == NotificationLevel.MUTED


class TestSetChannelOverride:
    """Tests for setting channel overrides."""

    def test_set_muted_override(self, notification_manager):
        """Test setting muted override."""
        override = notification_manager.set_channel_override(
            user_id=123, channel_id=456, level=NotificationLevel.MUTED
        )

        assert override.level == NotificationLevel.MUTED

    def test_set_mentions_only_override(self, notification_manager):
        """Test setting mentions-only override."""
        override = notification_manager.set_channel_override(
            user_id=123, channel_id=456, level=NotificationLevel.ONLY_MENTIONS
        )

        assert override.level == NotificationLevel.ONLY_MENTIONS

    def test_set_override_with_mute_expiration(self, notification_manager):
        """Test setting override with mute expiration."""
        future_time = int(time.time() * 1000) + 3600000

        override = notification_manager.set_channel_override(
            user_id=123,
            channel_id=456,
            level=NotificationLevel.MUTED,
            muted_until=future_time,
        )

        assert override.muted_until == future_time

    def test_update_existing_override(self, notification_manager):
        """Test updating existing override."""
        notification_manager.set_channel_override(
            user_id=123, channel_id=456, level=NotificationLevel.MUTED
        )

        override = notification_manager.set_channel_override(
            user_id=123, channel_id=456, level=NotificationLevel.ALL_MESSAGES
        )

        assert override.level == NotificationLevel.ALL_MESSAGES


class TestDeleteChannelOverride:
    """Tests for deleting channel overrides."""

    def test_delete_existing_override(self, notification_manager):
        """Test deleting existing override."""
        notification_manager.set_channel_override(
            user_id=123, channel_id=456, level=NotificationLevel.MUTED
        )

        result = notification_manager.delete_channel_override(123, 456)

        assert result is True

        override = notification_manager.get_channel_override(123, 456)
        assert override is None

    def test_delete_nonexistent_override(self, notification_manager):
        """Test deleting nonexistent override returns False."""
        result = notification_manager.delete_channel_override(123, 456)

        assert result is False


class TestChannelOverrideEffect:
    """Tests for channel override effects on notifications."""

    def test_muted_channel_blocks_notifications(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test muted channel blocks all notifications."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="testowner1",
                email="owner1@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="testmember1",
                email="member1@example.com",
                password="TestPass123!",
            )
            member2 = auth_manager.register(
                username="testmember2",
                email="member2@example.com",
                password="TestPass123!",
            )

        notification_manager.set_channel_override(
            user_id=member1.id, channel_id=999, level=NotificationLevel.MUTED
        )

        group = messaging_manager.create_group(
            owner.id, "Server Group", [member1.id, member2.id]
        )

        content = f"<@{member1.id}> check this"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=123,
            channel_id=999,
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id not in notified_users

    def test_nothing_level_blocks_notifications(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test NOTHING level blocks all notifications."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="testowner2",
                email="owner2@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="testmember3",
                email="member3@example.com",
                password="TestPass123!",
            )
            member2 = auth_manager.register(
                username="testmember4",
                email="member4@example.com",
                password="TestPass123!",
            )

        notification_manager.set_channel_override(
            user_id=member1.id, channel_id=999, level=NotificationLevel.NOTHING
        )

        group = messaging_manager.create_group(
            owner.id, "Server Group", [member1.id, member2.id]
        )

        content = f"<@{member1.id}>"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=123,
            channel_id=999,
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id not in notified_users

    def test_expired_mute_allows_notifications(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test expired mute allows notifications."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="testowner3",
                email="owner3@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="testmember5",
                email="member5@example.com",
                password="TestPass123!",
            )
            member2 = auth_manager.register(
                username="testmember6",
                email="member6@example.com",
                password="TestPass123!",
            )

        past_time = int(time.time() * 1000) - 3600000

        notification_manager.set_channel_override(
            user_id=member1.id,
            channel_id=999,
            level=NotificationLevel.MUTED,
            muted_until=past_time,
        )

        group = messaging_manager.create_group(
            owner.id, "Server Group", [member1.id, member2.id]
        )

        content = f"<@{member1.id}>"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=123,
            channel_id=999,
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id in notified_users
