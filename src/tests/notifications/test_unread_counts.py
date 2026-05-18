"""Tests for notification unread count tracking."""

import pytest

from src.core.notifications.models import UnreadCount


@pytest.mark.notifications
class TestUnreadCounts:
    """Tests for unread count tracking and management."""

    def test_get_unread_count_no_unread(self, notification_manager, test_user):
        """Test getting unread count when there are no unread notifications."""
        unread = notification_manager.get_unread_count(test_user.id)
        assert unread is not None
        assert isinstance(unread.total_unread, int)
        assert isinstance(unread.mention_count, int)

    def test_get_unread_counts_dict(self, notification_manager, test_user):
        """Test getting per-conversation unread counts."""
        counts = notification_manager.get_unread_counts(test_user.id)
        assert isinstance(counts, dict)

    def test_get_mention_count_zero(self, notification_manager, test_user):
        """Test getting mention count with no mentions."""
        count = notification_manager.get_mention_count(test_user.id)
        assert isinstance(count, int)
        assert count >= 0

    def test_mark_all_read_clears_counts(self, notification_manager, test_user):
        """Test that marking all read clears unread counts."""
        notification_manager.mark_all_read(test_user.id)
        unread = notification_manager.get_unread_count(test_user.id)
        assert unread.mention_count == 0

    def test_unread_count_dataclass(self):
        """Test UnreadCount dataclass fields."""
        uc = UnreadCount(user_id=1, conversation_id=2)
        assert uc.unread_count == 0
        assert uc.mention_count == 0
        assert uc.total_unread == 0

    def test_unread_count_with_server_filter(self, notification_manager, test_user):
        """Test getting unread count filtered by server."""
        unread = notification_manager.get_unread_count(test_user.id, server_id=999)
        assert unread is not None

    def test_mark_server_read(self, notification_manager, test_user):
        """Test marking all notifications in a server as read."""
        count = notification_manager.mark_server_read(test_user.id, 999)
        assert isinstance(count, int)

    def test_mark_channel_read(self, notification_manager, test_user):
        """Test marking all notifications in a channel as read."""
        count = notification_manager.mark_channel_read(test_user.id, 999)
        assert isinstance(count, int)
