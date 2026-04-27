"""
Tests for presence integration with relationships and servers.
"""

import pytest
from src.core.presence import UserStatus, ActivityType


class TestOnlineFriends:
    """Tests for getting online friends."""

    def test_get_online_friends_success(self, friends_pair):
        """Test getting online friends."""
        user1, user2, rel_manager, presence_manager = friends_pair

        presence_manager.set_status(user2.id, UserStatus.ONLINE)

        online = presence_manager.get_online_friends(user1.id)

        assert user2.id in online

    def test_get_online_friends_empty(self, friends_pair):
        """Test getting online friends when none online."""
        user1, user2, rel_manager, presence_manager = friends_pair

        presence_manager.set_status(user2.id, UserStatus.OFFLINE)

        online = presence_manager.get_online_friends(user1.id)

        assert user2.id not in online

    def test_get_online_friends_includes_idle(self, friends_pair):
        """Test that idle friends are included in online."""
        user1, user2, rel_manager, presence_manager = friends_pair

        presence_manager.set_status(user2.id, UserStatus.IDLE)

        online = presence_manager.get_online_friends(user1.id)

        assert user2.id in online

    def test_get_online_friends_includes_dnd(self, friends_pair):
        """Test that dnd friends are included in online."""
        user1, user2, rel_manager, presence_manager = friends_pair

        presence_manager.set_status(user2.id, UserStatus.DND)

        online = presence_manager.get_online_friends(user1.id)

        assert user2.id in online

    def test_get_online_friends_excludes_invisible(self, friends_pair):
        """Test that invisible friends are excluded from online."""
        user1, user2, rel_manager, presence_manager = friends_pair

        presence_manager.set_status(user2.id, UserStatus.INVISIBLE)

        online = presence_manager.get_online_friends(user1.id)

        # Invisible users are not in the online list
        assert user2.id not in online

    def test_get_online_friends_no_friends(self, fresh_users):
        """Test getting online friends when no friends."""
        user1, user2, presence_manager = fresh_users

        online = presence_manager.get_online_friends(user1.id)

        assert len(online) == 0

    @pytest.mark.skip("Complex integration test requires multiple modules")
    def test_get_online_friends_multiple(self, db_and_modules):
        """Test getting multiple online friends."""
        pass


class TestOnlineServerMembers:
    """Tests for getting online server members."""

    @pytest.mark.skip("Server member tests require server module integration")
    def test_get_online_server_members_success(self, users_with_server):
        """Test getting online server members."""
        pass

    @pytest.mark.skip("Server member tests require server module integration")
    def test_get_online_server_members_empty(self, users_with_server):
        """Test getting online members when none online."""
        pass

    @pytest.mark.skip("Server member tests require server module integration")
    def test_get_online_server_members_includes_idle(self, users_with_server):
        """Test that idle members are included."""
        pass

    @pytest.mark.skip("Server member tests require server module integration")
    def test_get_online_server_members_includes_dnd(self, users_with_server):
        """Test that dnd members are included."""
        pass

    @pytest.mark.skip("Server member tests require server module integration")
    def test_get_online_server_members_excludes_invisible(self, users_with_server):
        """Test that invisible members are excluded."""
        pass

    @pytest.mark.skip("Server member tests require server module integration")
    def test_get_online_server_members_multiple(self, users_with_server):
        """Test getting multiple online members."""
        pass


class TestBulkPresence:
    """Tests for bulk presence queries."""

    @pytest.mark.skip("Complex integration test requires multiple modules")
    def test_get_presences_success(self, db_and_modules):
        """Test getting multiple presences."""
        pass

    def test_get_presences_empty_list(self, fresh_users):
        """Test getting presences with empty list."""
        user1, user2, presence_manager = fresh_users

        presences = presence_manager.get_presences([])

        assert len(presences) == 0

    @pytest.mark.skip("Complex integration test requires multiple modules")
    def test_get_presences_mixed_statuses(self, db_and_modules):
        """Test getting presences with mixed statuses."""
        pass


class TestLastSeen:
    """Tests for last seen functionality."""

    def test_update_last_seen(self, fresh_users):
        """Test updating last seen timestamp."""
        user1, user2, presence_manager = fresh_users

        result = presence_manager.update_last_seen(user1.id)

        assert result.last_seen > 0

    def test_last_seen_updates_on_status_change(self, fresh_users):
        """Test that last seen updates on status change."""
        user1, user2, presence_manager = fresh_users

        result1 = presence_manager.set_status(user1.id, UserStatus.ONLINE)
        import time

        time.sleep(0.01)
        result2 = presence_manager.set_status(user1.id, UserStatus.IDLE)

        assert result2.last_seen >= result1.last_seen

    def test_last_seen_in_presence(self, fresh_users):
        """Test that last seen is included in presence."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        pres = presence_manager.get_presence(user1.id)

        assert pres.last_seen > 0


class TestPresenceWithActivity:
    """Tests for presence with activity integration."""

    def test_presence_includes_activity(self, fresh_users):
        """Test that presence includes activity."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        presence_manager.set_activity(user1.id, ActivityType.PLAYING, "Game")

        pres = presence_manager.get_presence(user1.id)

        assert pres.activity is not None
        assert pres.activity.name == "Game"

    def test_presence_includes_custom_status(self, fresh_users):
        """Test that presence includes custom status."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        presence_manager.set_custom_status(user1.id, "Working")

        pres = presence_manager.get_presence(user1.id)

        assert pres.custom_status is not None
        assert pres.custom_status.text == "Working"

    def test_full_presence_data(self, fresh_users):
        """Test getting full presence with all data."""
        user1, user2, presence_manager = fresh_users

        presence_manager.set_status(user1.id, UserStatus.ONLINE)
        presence_manager.set_custom_status(user1.id, "Coding", emoji=":computer:")
        presence_manager.set_activity(
            user1.id, ActivityType.PLAYING, "VS Code", details="Writing tests"
        )

        pres = presence_manager.get_presence(user1.id)

        assert pres.status == UserStatus.ONLINE
        assert pres.custom_status.text == "Coding"
        assert pres.custom_status.emoji == ":computer:"
        assert pres.activity.name == "VS Code"
        assert pres.activity.details == "Writing tests"
        assert pres.last_seen > 0
        assert pres.updated_at > 0
