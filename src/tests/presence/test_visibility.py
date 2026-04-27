"""
Tests for presence visibility rules.
"""

import pytest
from src.core.presence import UserStatus, ActivityType


class TestInvisibleMode:
    """Tests for invisible mode visibility."""

    def test_invisible_appears_offline_to_others(self, fresh_users):
        """Test that invisible users appear offline to others."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.INVISIBLE)

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.status == UserStatus.OFFLINE

    def test_invisible_sees_own_real_status(self, fresh_users):
        """Test that invisible users see their own real status."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.INVISIBLE)

        visible = presence_manager.get_visible_presence(user1.id, user1.id)

        assert visible.status == UserStatus.INVISIBLE

    def test_invisible_can_see_others(self, fresh_users):
        """Test that invisible users can see others' presence."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.INVISIBLE)
        presence_manager.set_status(user2.id, UserStatus.ONLINE)

        visible = presence_manager.get_visible_presence(user1.id, user2.id)

        assert visible.status == UserStatus.ONLINE

    def test_invisible_hides_activity(self, fresh_users):
        """Test that invisible mode hides activity from others."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.INVISIBLE)
        presence_manager.set_activity(user1.id, ActivityType.PLAYING, "Game")

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.activity is None

    def test_invisible_shows_custom_status(self, fresh_users):
        """Test that invisible mode still shows custom status."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.INVISIBLE)
        presence_manager.set_custom_status(user1.id, "Away")

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        # Custom status is shown even when appearing offline
        assert visible.custom_status is not None
        assert visible.custom_status.text == "Away"


class TestBlockedVisibility:
    """Tests for blocked user visibility."""

    @pytest.mark.skip(
        "Blocked visibility tests require relationships module integration"
    )
    def test_blocked_user_sees_offline(
        self, auth_manager, rel_manager, presence_manager
    ):
        """Test that blocked user sees target as offline."""
        pass

    @pytest.mark.skip(
        "Blocked visibility tests require relationships module integration"
    )
    def test_blocker_sees_blocked_as_offline(
        self, auth_manager, rel_manager, presence_manager
    ):
        """Test that blocker sees blocked user as offline."""
        pass

    @pytest.mark.skip(
        "Blocked visibility tests require relationships module integration"
    )
    def test_blocked_hides_activity(self, auth_manager, rel_manager, presence_manager):
        """Test that blocking hides activity."""
        pass

    @pytest.mark.skip(
        "Blocked visibility tests require relationships module integration"
    )
    def test_blocked_hides_custom_status(
        self, auth_manager, rel_manager, presence_manager
    ):
        """Test that blocking hides custom status."""
        pass

    @pytest.mark.skip(
        "Blocked visibility tests require relationships module integration"
    )
    def test_blocked_hides_last_seen(self, auth_manager, rel_manager, presence_manager):
        """Test that blocking hides last seen."""
        pass


class TestCanSeePresence:
    """Tests for can_see_presence function."""

    def test_can_see_own_presence(self, fresh_users):
        """Test that user can always see own presence."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.INVISIBLE)

        can_see = presence_manager.can_see_presence(user1.id, user1.id)

        assert can_see is True

    def test_can_see_online_user(self, fresh_users):
        """Test that user can see online user's presence."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user2.id, UserStatus.ONLINE)

        can_see = presence_manager.can_see_presence(user1.id, user2.id)

        assert can_see is True

    def test_cannot_see_invisible_user(self, fresh_users):
        """Test that user cannot see invisible user's presence."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user2.id, UserStatus.INVISIBLE)

        can_see = presence_manager.can_see_presence(user1.id, user2.id)

        assert can_see is False

    @pytest.mark.skip(
        "Blocked visibility tests require relationships module integration"
    )
    def test_cannot_see_blocked_user(self, auth_manager, rel_manager, presence_manager):
        """Test that blocked user cannot see blocker's presence."""
        pass

    @pytest.mark.skip(
        "Blocked visibility tests require relationships module integration"
    )
    def test_blocker_cannot_see_blocked(
        self, auth_manager, rel_manager, presence_manager
    ):
        """Test that blocker cannot see blocked user's presence."""
        pass


class TestFriendsVisibility:
    """Tests for friends visibility."""

    def test_friends_can_see_each_other(self, friends_pair):
        """Test that friends can see each other's presence."""
        user1, user2, rel_manager, presence_manager = friends_pair

        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        presence_manager.set_status(user2.id, UserStatus.ONLINE)

        visible1 = presence_manager.get_visible_presence(user2.id, user1.id)
        visible2 = presence_manager.get_visible_presence(user1.id, user2.id)

        assert visible1.status == UserStatus.ONLINE
        assert visible2.status == UserStatus.ONLINE

    def test_friends_see_activity(self, friends_pair):
        """Test that friends can see each other's activity."""
        user1, user2, rel_manager, presence_manager = friends_pair

        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        presence_manager.set_activity(user1.id, ActivityType.PLAYING, "Game")

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.activity is not None
        assert visible.activity.name == "Game"

    def test_invisible_friend_appears_offline(self, friends_pair):
        """Test that invisible friend appears offline."""
        user1, user2, rel_manager, presence_manager = friends_pair

        presence_manager.set_status(user1.id, UserStatus.INVISIBLE)

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.status == UserStatus.OFFLINE


class TestNormalVisibility:
    """Tests for normal (non-blocked, non-invisible) visibility."""

    def test_online_visible(self, fresh_users):
        """Test that online status is visible."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.ONLINE)

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.status == UserStatus.ONLINE

    def test_idle_visible(self, fresh_users):
        """Test that idle status is visible."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.IDLE)

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.status == UserStatus.IDLE

    def test_dnd_visible(self, fresh_users):
        """Test that dnd status is visible."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.DND)

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.status == UserStatus.DND

    def test_offline_visible(self, fresh_users):
        """Test that offline status is visible."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.OFFLINE)

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.status == UserStatus.OFFLINE

    def test_activity_visible(self, fresh_users):
        """Test that activity is visible."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        presence_manager.set_activity(user1.id, ActivityType.PLAYING, "Game")

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.activity is not None

    def test_custom_status_visible(self, fresh_users):
        """Test that custom status is visible."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        presence_manager.set_custom_status(user1.id, "Working")

        visible = presence_manager.get_visible_presence(user2.id, user1.id)

        assert visible.custom_status is not None
        assert visible.custom_status.text == "Working"
