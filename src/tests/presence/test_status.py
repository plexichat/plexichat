"""
Tests for user status management.
"""

from src.core.presence import (
    UserStatus,
)


class TestSetStatus:
    """Tests for setting user status."""

    def test_set_status_online(self, fresh_users):
        """Test setting status to online."""
        user1, user2, presence = fresh_users

        result = presence.set_status(user1.id, UserStatus.ONLINE)

        assert result.status == UserStatus.ONLINE
        assert result.user_id == user1.id

    def test_set_status_idle(self, fresh_users):
        """Test setting status to idle."""
        user1, user2, presence = fresh_users

        result = presence.set_status(user1.id, UserStatus.IDLE)

        assert result.status == UserStatus.IDLE

    def test_set_status_dnd(self, fresh_users):
        """Test setting status to do not disturb."""
        user1, user2, presence = fresh_users

        result = presence.set_status(user1.id, UserStatus.DND)

        assert result.status == UserStatus.DND

    def test_set_status_invisible(self, fresh_users):
        """Test setting status to invisible."""
        user1, user2, presence = fresh_users

        result = presence.set_status(user1.id, UserStatus.INVISIBLE)

        assert result.status == UserStatus.INVISIBLE

    def test_set_status_offline(self, fresh_users):
        """Test setting status to offline."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        result = presence.set_status(user1.id, UserStatus.OFFLINE)

        assert result.status == UserStatus.OFFLINE

    def test_set_status_updates_last_seen(self, fresh_users):
        """Test that setting status updates last_seen."""
        user1, user2, presence = fresh_users

        result = presence.set_status(user1.id, UserStatus.ONLINE)

        assert result.last_seen > 0

    def test_set_status_updates_updated_at(self, fresh_users):
        """Test that setting status updates updated_at."""
        user1, user2, presence = fresh_users

        result = presence.set_status(user1.id, UserStatus.ONLINE)

        assert result.updated_at > 0


class TestGetStatus:
    """Tests for getting user status."""

    def test_get_status_after_set(self, fresh_users):
        """Test getting status after setting it."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        status = presence.get_status(user1.id)

        assert status == UserStatus.ONLINE

    def test_get_status_default_offline(self, fresh_users):
        """Test that default status is offline."""
        user1, user2, presence = fresh_users

        status = presence.get_status(user2.id)

        assert status == UserStatus.OFFLINE

    def test_get_status_after_change(self, fresh_users):
        """Test getting status after changing it."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        presence.set_status(user1.id, UserStatus.IDLE)
        status = presence.get_status(user1.id)

        assert status == UserStatus.IDLE


class TestClearStatus:
    """Tests for clearing user status."""

    def test_clear_status_sets_offline(self, fresh_users):
        """Test that clearing status sets it to offline."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        result = presence.clear_status(user1.id)

        assert result.status == UserStatus.OFFLINE

    def test_clear_status_already_offline(self, fresh_users):
        """Test clearing status when already offline."""
        user1, user2, presence = fresh_users

        result = presence.clear_status(user1.id)

        assert result.status == UserStatus.OFFLINE


class TestStatusTransitions:
    """Tests for status transitions."""

    def test_online_to_idle(self, fresh_users):
        """Test transitioning from online to idle."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        result = presence.set_status(user1.id, UserStatus.IDLE)

        assert result.status == UserStatus.IDLE

    def test_idle_to_online(self, fresh_users):
        """Test transitioning from idle to online."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.IDLE)
        result = presence.set_status(user1.id, UserStatus.ONLINE)

        assert result.status == UserStatus.ONLINE

    def test_online_to_dnd(self, fresh_users):
        """Test transitioning from online to dnd."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        result = presence.set_status(user1.id, UserStatus.DND)

        assert result.status == UserStatus.DND

    def test_dnd_to_online(self, fresh_users):
        """Test transitioning from dnd to online."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.DND)
        result = presence.set_status(user1.id, UserStatus.ONLINE)

        assert result.status == UserStatus.ONLINE

    def test_online_to_invisible(self, fresh_users):
        """Test transitioning from online to invisible."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        result = presence.set_status(user1.id, UserStatus.INVISIBLE)

        assert result.status == UserStatus.INVISIBLE

    def test_invisible_to_online(self, fresh_users):
        """Test transitioning from invisible to online."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.INVISIBLE)
        result = presence.set_status(user1.id, UserStatus.ONLINE)

        assert result.status == UserStatus.ONLINE

    def test_offline_to_online(self, fresh_users):
        """Test transitioning from offline to online."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.OFFLINE)
        result = presence.set_status(user1.id, UserStatus.ONLINE)

        assert result.status == UserStatus.ONLINE

    def test_multiple_transitions(self, fresh_users):
        """Test multiple status transitions."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        presence.set_status(user1.id, UserStatus.IDLE)
        presence.set_status(user1.id, UserStatus.DND)
        presence.set_status(user1.id, UserStatus.INVISIBLE)
        result = presence.set_status(user1.id, UserStatus.OFFLINE)

        assert result.status == UserStatus.OFFLINE


class TestCustomStatus:
    """Tests for custom status messages."""

    def test_set_custom_status_text_only(self, fresh_users):
        """Test setting custom status with text only."""
        user1, user2, presence = fresh_users

        result = presence.set_custom_status(user1.id, "Working on a project")

        assert result.custom_status is not None
        assert result.custom_status.text == "Working on a project"

    def test_set_custom_status_with_emoji(self, fresh_users):
        """Test setting custom status with emoji."""
        user1, user2, presence = fresh_users

        result = presence.set_custom_status(user1.id, "Coding", emoji=":computer:")

        assert result.custom_status.text == "Coding"
        assert result.custom_status.emoji == ":computer:"

    def test_set_custom_status_with_expiration(self, fresh_users):
        """Test setting custom status with expiration."""
        user1, user2, presence = fresh_users
        import time
        expires_at = int(time.time() * 1000) + 3600000  # 1 hour from now

        result = presence.set_custom_status(user1.id, "BRB", expires_at=expires_at)

        assert result.custom_status.expires_at == expires_at

    def test_get_custom_status(self, fresh_users):
        """Test getting custom status."""
        user1, user2, presence = fresh_users

        presence.set_custom_status(user1.id, "Testing")
        custom = presence.get_custom_status(user1.id)

        assert custom is not None
        assert custom.text == "Testing"

    def test_get_custom_status_none(self, fresh_users):
        """Test getting custom status when none set."""
        user1, user2, presence = fresh_users

        custom = presence.get_custom_status(user2.id)

        assert custom is None

    def test_clear_custom_status(self, fresh_users):
        """Test clearing custom status."""
        user1, user2, presence = fresh_users

        presence.set_custom_status(user1.id, "Testing")
        result = presence.clear_custom_status(user1.id)

        assert result.custom_status is None

    def test_update_custom_status(self, fresh_users):
        """Test updating custom status."""
        user1, user2, presence = fresh_users

        presence.set_custom_status(user1.id, "First status")
        result = presence.set_custom_status(user1.id, "Updated status")

        assert result.custom_status.text == "Updated status"

    def test_custom_status_created_at(self, fresh_users):
        """Test that custom status has created_at timestamp."""
        user1, user2, presence = fresh_users

        result = presence.set_custom_status(user1.id, "Testing")

        assert result.custom_status.created_at > 0
