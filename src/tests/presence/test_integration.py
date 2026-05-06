"""Tests for presence integration with messaging and servers."""

import pytest

from src.core.presence.models import UserStatus


@pytest.mark.presence
class TestIntegration:
    """Tests for presence integration with other modules."""

    def test_set_status_online(self, presence_manager, test_user):
        """Test setting user status to online."""
        presence = presence_manager.set_status(test_user.id, UserStatus.ONLINE)
        assert presence.status == UserStatus.ONLINE

    def test_set_status_idle(self, presence_manager, test_user):
        """Test setting user status to idle."""
        presence = presence_manager.set_status(test_user.id, UserStatus.IDLE)
        assert presence.status == UserStatus.IDLE

    def test_set_status_dnd(self, presence_manager, test_user):
        """Test setting user status to DND."""
        presence = presence_manager.set_status(test_user.id, UserStatus.DND)
        assert presence.status == UserStatus.DND

    def test_set_status_offline(self, presence_manager, test_user):
        """Test setting user status to offline."""
        presence = presence_manager.set_status(test_user.id, UserStatus.OFFLINE)
        assert presence.status == UserStatus.OFFLINE

    def test_set_status_invisible(self, presence_manager, test_user):
        """Test setting user status to invisible."""
        presence = presence_manager.set_status(test_user.id, UserStatus.INVISIBLE)
        assert presence.status == UserStatus.INVISIBLE

    def test_set_status_by_string(self, presence_manager, test_user):
        """Test setting status using string value."""
        presence = presence_manager.set_status(test_user.id, "online")
        assert presence.status == UserStatus.ONLINE

    def test_set_invalid_status_raises(self, presence_manager, test_user):
        """Test that invalid status raises InvalidStatusError."""
        from src.core.presence.exceptions import InvalidStatusError

        with pytest.raises(InvalidStatusError):
            presence_manager.set_status(test_user.id, "invalid_status")

    def test_get_presence(self, presence_manager, test_user):
        """Test getting full presence for a user."""
        presence_manager.set_status(test_user.id, UserStatus.ONLINE)
        presence = presence_manager.get_presence(test_user.id)
        assert presence is not None
        assert presence.user_id == test_user.id
        assert presence.status == UserStatus.ONLINE

    def test_get_multiple_presences(self, presence_manager, two_users):
        """Test getting presence for multiple users."""
        user1, user2 = two_users
        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        presence_manager.set_status(user2.id, UserStatus.IDLE)
        presences = presence_manager.get_presences([user1.id, user2.id])
        assert len(presences) == 2

    def test_clear_status(self, presence_manager, test_user):
        """Test clearing user status (set to offline)."""
        presence_manager.set_status(test_user.id, UserStatus.ONLINE)
        presence = presence_manager.clear_status(test_user.id)
        assert presence.status == UserStatus.OFFLINE

    def test_update_last_seen(self, presence_manager, test_user):
        """Test updating user's last seen timestamp."""
        presence = presence_manager.update_last_seen(test_user.id)
        assert presence is not None
        assert presence.last_seen > 0
