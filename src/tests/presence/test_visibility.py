"""Tests for presence visibility rules (invisible mode, blocking)."""

import pytest

from src.core.presence.models import UserStatus


@pytest.mark.presence
class TestVisibility:
    """Tests for presence visibility and privacy rules."""

    def test_invisible_appears_offline_to_others(self, presence_manager, two_users):
        """Test that invisible users appear offline to others."""
        user1, user2 = two_users
        presence_manager.set_status(user1.id, UserStatus.INVISIBLE)
        visible = presence_manager.get_visible_presence(user2.id, user1.id)
        assert visible.status == UserStatus.OFFLINE

    def test_invisible_sees_own_real_status(self, presence_manager, test_user):
        """Test that invisible users see their own real status."""
        presence_manager.set_status(test_user.id, UserStatus.INVISIBLE)
        visible = presence_manager.get_visible_presence(test_user.id, test_user.id)
        assert visible.status == UserStatus.INVISIBLE

    def test_blocked_user_appears_offline(
        self, presence_manager, rel_manager, two_users
    ):
        """Test that blocked users appear offline to each other."""
        user1, user2 = two_users
        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        rel_manager.block_user(user1.id, user2.id)
        visible = presence_manager.get_visible_presence(user2.id, user1.id)
        assert visible.status == UserStatus.OFFLINE

    def test_can_see_own_presence(self, presence_manager, test_user):
        """Test that a user can always see their own real presence."""
        presence_manager.set_status(test_user.id, UserStatus.DND)
        assert presence_manager.can_see_presence(test_user.id, test_user.id) is True

    def test_cannot_see_invisible_user(self, presence_manager, two_users):
        """Test that cannot see real presence of invisible user."""
        user1, user2 = two_users
        presence_manager.set_status(user1.id, UserStatus.INVISIBLE)
        assert presence_manager.can_see_presence(user2.id, user1.id) is False

    def test_online_visible_to_others(self, presence_manager, two_users):
        """Test that online users are visible to others."""
        user1, user2 = two_users
        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        visible = presence_manager.get_visible_presence(user2.id, user1.id)
        assert visible.status == UserStatus.ONLINE

    def test_dnd_visible_to_others(self, presence_manager, two_users):
        """Test that DND users are visible to others."""
        user1, user2 = two_users
        presence_manager.set_status(user1.id, UserStatus.DND)
        visible = presence_manager.get_visible_presence(user2.id, user1.id)
        assert visible.status == UserStatus.DND

    def test_idle_visible_to_others(self, presence_manager, two_users):
        """Test that idle users are visible to others."""
        user1, user2 = two_users
        presence_manager.set_status(user1.id, UserStatus.IDLE)
        visible = presence_manager.get_visible_presence(user2.id, user1.id)
        assert visible.status == UserStatus.IDLE

    def test_bulk_visibility(self, presence_manager, three_users):
        """Test bulk visible presence retrieval."""
        user1, user2, user3 = three_users
        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        presence_manager.set_status(user2.id, UserStatus.INVISIBLE)
        presence_manager.set_status(user3.id, UserStatus.IDLE)
        result = presence_manager.get_visible_presences_bulk(
            user1.id, [user1.id, user2.id, user3.id]
        )
        assert len(result) == 3
        # User2 is invisible, should appear offline
        assert result[user2.id].status == UserStatus.OFFLINE
