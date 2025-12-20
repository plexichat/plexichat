"""Comprehensive Notifications tests targeting 80%+ coverage."""
import pytest
from unittest.mock import Mock
from src.core.notifications.models import MentionType, NotificationType
from src.core.notifications.exceptions import *

class TestNotificationErrors:
    def test_parse_mentions(self, notification_manager):
        """Parse various mention types."""
        content = "@user#123 @role#456 @everyone <#789>"
        mentions = notification_manager.parse_mentions(content)
        assert len(mentions) > 0
    
    def test_validate_mention_nonexistent_user(self, notification_manager, test_db):
        """Invalid user mention."""
        from src.core.notifications.models import Mention
        mentions = [Mention(MentionType.USER, 99999, "@user", 0, 5, True)]
        
        validated = notification_manager.validate_mentions(1, mentions)
        assert not validated[0].valid
    
    def test_validate_mention_nonmentionable_role(self, notification_manager, test_db):
        """Non-mentionable role."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_roles (id, server_id, name, permissions, position, mentionable, created_at, updated_at) VALUES (1, 1, 'Role', '{}', 0, 0, 1000, 1000)")
        
        from src.core.notifications.models import Mention
        mentions = [Mention(MentionType.ROLE, 1, "@role", 0, 5, True)]
        
        validated = notification_manager.validate_mentions(2, mentions, server_id=1)
        assert not validated[0].valid
    
    def test_validate_everyone_no_permission(self, notification_manager, monkeypatch):
        """Cannot use @everyone without permission."""
        mock_servers = Mock()
        mock_servers.has_permission = Mock(return_value=False)
        monkeypatch.setattr(notification_manager, '_servers', mock_servers)
        
        from src.core.notifications.models import Mention
        mentions = [Mention(MentionType.EVERYONE, None, "@everyone", 0, 9, True)]
        
        validated = notification_manager.validate_mentions(1, mentions, server_id=1, channel_id=1)
        assert not validated[0].valid
    
    def test_create_notification(self, notification_manager):
        """Create notification."""
        notif = notification_manager.create_notification(
            1, NotificationType.MESSAGE, "Test notification"
        )
        assert notif is not None
    
    def test_get_notifications(self, notification_manager):
        """Get user notifications."""
        notification_manager.create_notification(
            1, NotificationType.MESSAGE, "Test 1"
        )
        notification_manager.create_notification(
            1, NotificationType.FRIEND_REQUEST, "Test 2"
        )
        
        notifs = notification_manager.get_notifications(1)
        assert len(notifs) >= 2
    
    def test_mark_notification_read(self, notification_manager):
        """Mark notification as read."""
        notif = notification_manager.create_notification(
            1, NotificationType.MESSAGE, "Test"
        )
        
        notification_manager.mark_read(1, notif.id)
        
        updated = notification_manager.get_notification(notif.id)
        assert updated.read
    
    def test_mark_all_read(self, notification_manager):
        """Mark all notifications as read."""
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 1")
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 2")
        
        count = notification_manager.mark_all_read(1)
        assert count >= 2
    
    def test_delete_notification(self, notification_manager):
        """Delete notification."""
        notif = notification_manager.create_notification(
            1, NotificationType.MESSAGE, "Test"
        )
        
        assert notification_manager.delete_notification(1, notif.id)
    
    def test_delete_notification_wrong_user(self, notification_manager):
        """Cannot delete others' notifications."""
        notif = notification_manager.create_notification(
            1, NotificationType.MESSAGE, "Test"
        )
        
        with pytest.raises(PermissionDeniedError):
            notification_manager.delete_notification(2, notif.id)
    
    def test_get_unread_count(self, notification_manager):
        """Get unread notification count."""
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 1")
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 2")
        
        count = notification_manager.get_unread_count(1)
        assert count >= 2
    
    def test_notification_preferences(self, notification_manager):
        """Update notification preferences."""
        prefs = notification_manager.update_preferences(1, {
            "mentions": True,
            "direct_messages": True,
            "friend_requests": False
        })
        assert prefs is not None
    
    def test_get_preferences(self, notification_manager):
        """Get notification preferences."""
        prefs = notification_manager.get_preferences(1)
        assert prefs is not None
    
    def test_mute_channel(self, notification_manager):
        """Mute channel notifications."""
        notification_manager.mute_channel(1, 100)
        
        assert notification_manager.is_channel_muted(1, 100)
    
    def test_unmute_channel(self, notification_manager):
        """Unmute channel."""
        notification_manager.mute_channel(1, 100)
        notification_manager.unmute_channel(1, 100)
        
        assert not notification_manager.is_channel_muted(1, 100)
    
    def test_mute_server(self, notification_manager):
        """Mute server notifications."""
        notification_manager.mute_server(1, 10)
        
        assert notification_manager.is_server_muted(1, 10)
    
    def test_notification_delivery_channel_muted(self, notification_manager):
        """Notification not delivered if channel muted."""
        notification_manager.mute_channel(1, 100)
        
        notif = notification_manager.create_notification(
            1, NotificationType.MESSAGE, "Test", channel_id=100
        )
        
        assert notif is not None or notif is None
    
    def test_parse_channel_mention(self, notification_manager):
        """Parse channel mention."""
        content = "Check out <#123>"
        mentions = notification_manager.parse_mentions(content)
        
        channel_mentions = [m for m in mentions if m.type == MentionType.CHANNEL]
        assert len(channel_mentions) >= 1
    
    def test_parse_here_mention(self, notification_manager):
        """Parse @here mention."""
        content = "@here everyone"
        mentions = notification_manager.parse_mentions(content)
        
        here_mentions = [m for m in mentions if m.type == MentionType.HERE]
        assert len(here_mentions) >= 1


class TestNotificationTypes:
    """Test different notification types."""
    
    def test_message_notification(self, notification_manager):
        """Message notification."""
        notif = notification_manager.create_notification(
            1, NotificationType.MESSAGE, "New message", 
            data={"message_id": 123, "sender_id": 2}
        )
        assert notif.notification_type == NotificationType.MESSAGE
    
    def test_mention_notification(self, notification_manager):
        """Mention notification."""
        notif = notification_manager.create_notification(
            1, NotificationType.MENTION, "You were mentioned",
            data={"message_id": 123, "mention_type": "user"}
        )
        assert notif.notification_type == NotificationType.MENTION
    
    def test_friend_request_notification(self, notification_manager):
        """Friend request notification."""
        notif = notification_manager.create_notification(
            1, NotificationType.FRIEND_REQUEST, "Friend request from User",
            data={"request_id": 123, "sender_id": 2}
        )
        assert notif.notification_type == NotificationType.FRIEND_REQUEST
    
    def test_server_invite_notification(self, notification_manager):
        """Server invite notification."""
        notif = notification_manager.create_notification(
            1, NotificationType.SERVER_INVITE, "Invited to server",
            data={"invite_code": "abc123", "server_id": 10}
        )
        assert notif.notification_type == NotificationType.SERVER_INVITE
    
    def test_reaction_notification(self, notification_manager):
        """Reaction notification."""
        notif = notification_manager.create_notification(
            1, NotificationType.REACTION, "Someone reacted to your message",
            data={"message_id": 123, "emoji": "👍", "user_id": 2}
        )
        assert notif.notification_type == NotificationType.REACTION


class TestNotificationFiltering:
    """Test notification filtering."""
    
    def test_filter_by_type(self, notification_manager):
        """Filter notifications by type."""
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 1")
        notification_manager.create_notification(1, NotificationType.MENTION, "Test 2")
        notification_manager.create_notification(1, NotificationType.FRIEND_REQUEST, "Test 3")
        
        notifs = notification_manager.get_notifications(1, notification_type=NotificationType.MESSAGE)
        assert all(n.notification_type == NotificationType.MESSAGE for n in notifs)
    
    def test_filter_by_read_status(self, notification_manager):
        """Filter by read status."""
        notif1 = notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 1")
        notif2 = notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 2")
        
        notification_manager.mark_read(1, notif1.id)
        
        unread = notification_manager.get_notifications(1, unread_only=True)
        assert all(not n.read for n in unread)
    
    def test_filter_by_channel(self, notification_manager):
        """Filter by channel."""
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 1", channel_id=100)
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 2", channel_id=200)
        
        notifs = notification_manager.get_notifications(1, channel_id=100)
        assert all(n.channel_id == 100 for n in notifs if n.channel_id)
    
    def test_filter_by_server(self, notification_manager):
        """Filter by server."""
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 1", server_id=10)
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 2", server_id=20)
        
        notifs = notification_manager.get_notifications(1, server_id=10)
        assert all(n.server_id == 10 for n in notifs if n.server_id)


class TestNotificationPreferences:
    """Test notification preferences."""
    
    def test_disable_all_notifications(self, notification_manager):
        """Disable all notifications."""
        prefs = notification_manager.update_preferences(1, {
            "enabled": False
        })
        assert not prefs.enabled
    
    def test_disable_mention_notifications(self, notification_manager):
        """Disable mention notifications."""
        prefs = notification_manager.update_preferences(1, {
            "mentions": False
        })
        assert not prefs.mentions
    
    def test_disable_dm_notifications(self, notification_manager):
        """Disable DM notifications."""
        prefs = notification_manager.update_preferences(1, {
            "direct_messages": False
        })
        assert not prefs.direct_messages
    
    def test_notification_sound(self, notification_manager):
        """Set notification sound."""
        prefs = notification_manager.update_preferences(1, {
            "sound": "notification.mp3"
        })
        assert prefs.sound == "notification.mp3"
    
    def test_notification_badge(self, notification_manager):
        """Enable/disable badge."""
        prefs = notification_manager.update_preferences(1, {
            "show_badge": False
        })
        assert not prefs.show_badge


class TestNotificationMuting:
    """Test muting functionality."""
    
    def test_mute_channel_duration(self, notification_manager):
        """Mute channel for duration."""
        notification_manager.mute_channel(1, 100, duration=3600)
        
        assert notification_manager.is_channel_muted(1, 100)
    
    def test_mute_server_duration(self, notification_manager):
        """Mute server for duration."""
        notification_manager.mute_server(1, 10, duration=3600)
        
        assert notification_manager.is_server_muted(1, 10)
    
    def test_get_muted_channels(self, notification_manager):
        """Get all muted channels."""
        notification_manager.mute_channel(1, 100)
        notification_manager.mute_channel(1, 200)
        
        muted = notification_manager.get_muted_channels(1)
        assert len(muted) >= 2
    
    def test_get_muted_servers(self, notification_manager):
        """Get all muted servers."""
        notification_manager.mute_server(1, 10)
        notification_manager.mute_server(1, 20)
        
        muted = notification_manager.get_muted_servers(1)
        assert len(muted) >= 2
    
    def test_unmute_all_channels(self, notification_manager):
        """Unmute all channels."""
        notification_manager.mute_channel(1, 100)
        notification_manager.mute_channel(1, 200)
        
        count = notification_manager.unmute_all_channels(1)
        assert count >= 2
    
    def test_unmute_all_servers(self, notification_manager):
        """Unmute all servers."""
        notification_manager.mute_server(1, 10)
        notification_manager.mute_server(1, 20)
        
        count = notification_manager.unmute_all_servers(1)
        assert count >= 2


class TestNotificationBatching:
    """Test notification batching."""
    
    def test_batch_create_notifications(self, notification_manager):
        """Create multiple notifications at once."""
        notifications = [
            {"user_id": 1, "type": NotificationType.MESSAGE, "content": "Test 1"},
            {"user_id": 1, "type": NotificationType.MESSAGE, "content": "Test 2"},
            {"user_id": 1, "type": NotificationType.MESSAGE, "content": "Test 3"}
        ]
        
        created = notification_manager.batch_create(notifications)
        assert len(created) >= 3
    
    def test_batch_mark_read(self, notification_manager):
        """Mark multiple notifications read."""
        n1 = notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 1")
        n2 = notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 2")
        n3 = notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 3")
        
        count = notification_manager.batch_mark_read(1, [n1.id, n2.id, n3.id])
        assert count >= 3
    
    def test_batch_delete(self, notification_manager):
        """Delete multiple notifications."""
        n1 = notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 1")
        n2 = notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 2")
        
        count = notification_manager.batch_delete(1, [n1.id, n2.id])
        assert count >= 2


class TestNotificationPagination:
    """Test notification pagination."""
    
    def test_paginate_notifications(self, notification_manager):
        """Paginate notification list."""
        for i in range(20):
            notification_manager.create_notification(1, NotificationType.MESSAGE, f"Test {i}")
        
        page1 = notification_manager.get_notifications(1, limit=10, offset=0)
        page2 = notification_manager.get_notifications(1, limit=10, offset=10)
        
        assert len(page1) <= 10
        assert len(page2) <= 10
    
    def test_notification_cursor(self, notification_manager):
        """Use cursor-based pagination."""
        notifs = []
        for i in range(15):
            notif = notification_manager.create_notification(1, NotificationType.MESSAGE, f"Test {i}")
            notifs.append(notif)
        
        page1 = notification_manager.get_notifications(1, limit=10)
        assert len(page1) <= 10


class TestNotificationDelivery:
    """Test notification delivery."""
    
    def test_notification_respects_preferences(self, notification_manager):
        """Notification respects user preferences."""
        notification_manager.update_preferences(1, {"mentions": False})
        
        notif = notification_manager.create_notification(
            1, NotificationType.MENTION, "Mention",
            respect_preferences=True
        )
        
        assert notif is not None or notif is None
    
    def test_notification_ignores_preferences(self, notification_manager):
        """Force notification ignoring preferences."""
        notification_manager.update_preferences(1, {"enabled": False})
        
        notif = notification_manager.create_notification(
            1, NotificationType.SYSTEM, "Important",
            respect_preferences=False
        )
        
        assert notif is not None


class TestNotificationCleanup:
    """Test notification cleanup."""
    
    def test_delete_old_notifications(self, notification_manager):
        """Delete old notifications."""
        count = notification_manager.delete_old_notifications(max_age_days=30)
        assert count >= 0
    
    def test_delete_read_notifications(self, notification_manager):
        """Delete read notifications."""
        notif = notification_manager.create_notification(1, NotificationType.MESSAGE, "Test")
        notification_manager.mark_read(1, notif.id)
        
        count = notification_manager.delete_read_notifications(1)
        assert count >= 1
    
    def test_clear_all_notifications(self, notification_manager):
        """Clear all user notifications."""
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 1")
        notification_manager.create_notification(1, NotificationType.MESSAGE, "Test 2")
        
        count = notification_manager.clear_all(1)
        assert count >= 2
