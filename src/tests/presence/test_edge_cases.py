"""
Tests for edge cases and error handling.
"""

import pytest
from src.core.presence import (
    UserStatus,
    ActivityType,
    UserNotFoundError,
    InvalidActivityError,
)


class TestUserNotFound:
    """Tests for user not found scenarios."""

    def test_set_status_invalid_user(self, db_and_modules):
        """Test setting status for non-existent user."""
        db, auth, servers, relationships, presence = db_and_modules

        with pytest.raises(UserNotFoundError):
            presence.set_status(999999999, UserStatus.ONLINE)

    def test_set_custom_status_invalid_user(self, db_and_modules):
        """Test setting custom status for non-existent user."""
        db, auth, servers, relationships, presence = db_and_modules

        with pytest.raises(UserNotFoundError):
            presence.set_custom_status(999999999, "Status")

    def test_set_activity_invalid_user(self, db_and_modules):
        """Test setting activity for non-existent user."""
        db, auth, servers, relationships, presence = db_and_modules

        with pytest.raises(UserNotFoundError):
            presence.set_activity(999999999, ActivityType.PLAYING, "Game")

    def test_clear_status_invalid_user(self, db_and_modules):
        """Test clearing status for non-existent user."""
        db, auth, servers, relationships, presence = db_and_modules

        with pytest.raises(UserNotFoundError):
            presence.clear_status(999999999)

    def test_clear_custom_status_invalid_user(self, db_and_modules):
        """Test clearing custom status for non-existent user."""
        db, auth, servers, relationships, presence = db_and_modules

        with pytest.raises(UserNotFoundError):
            presence.clear_custom_status(999999999)

    def test_clear_activity_invalid_user(self, db_and_modules):
        """Test clearing activity for non-existent user."""
        db, auth, servers, relationships, presence = db_and_modules

        with pytest.raises(UserNotFoundError):
            presence.clear_activity(999999999)

    def test_update_last_seen_invalid_user(self, db_and_modules):
        """Test updating last seen for non-existent user."""
        db, auth, servers, relationships, presence = db_and_modules

        with pytest.raises(UserNotFoundError):
            presence.update_last_seen(999999999)

    def test_start_typing_invalid_user(self, db_and_modules):
        """Test starting typing for non-existent user."""
        db, auth, servers, relationships, presence = db_and_modules

        with pytest.raises(UserNotFoundError):
            presence.start_typing(999999999, 12345)


class TestInvalidActivity:
    """Tests for invalid activity scenarios."""

    def test_activity_empty_name(self, fresh_users):
        """Test activity with empty name."""
        user1, user2, presence = fresh_users

        with pytest.raises(InvalidActivityError):
            presence.set_activity(user1.id, ActivityType.PLAYING, "")

    def test_activity_whitespace_name(self, fresh_users):
        """Test activity with whitespace-only name."""
        user1, user2, presence = fresh_users

        with pytest.raises(InvalidActivityError):
            presence.set_activity(user1.id, ActivityType.PLAYING, "   ")


class TestDefaultPresence:
    """Tests for default presence values."""

    def test_default_status_offline(self, fresh_users):
        """Test that default status is offline."""
        user1, user2, presence = fresh_users

        status = presence.get_status(user1.id)

        assert status == UserStatus.OFFLINE

    def test_default_presence_values(self, fresh_users):
        """Test default presence values for new user."""
        user1, user2, presence = fresh_users

        pres = presence.get_presence(user1.id)

        assert pres.status == UserStatus.OFFLINE
        assert pres.custom_status is None
        assert pres.activity is None
        assert pres.last_seen == 0

    def test_default_custom_status_none(self, fresh_users):
        """Test that default custom status is None."""
        user1, user2, presence = fresh_users

        custom = presence.get_custom_status(user1.id)

        assert custom is None

    def test_default_activity_none(self, fresh_users):
        """Test that default activity is None."""
        user1, user2, presence = fresh_users

        activity = presence.get_activity(user1.id)

        assert activity is None


class TestPresenceRecordCreation:
    """Tests for presence record creation."""

    def test_presence_record_created_on_set_status(self, fresh_users):
        """Test that presence record is created on first status set."""
        user1, user2, presence = fresh_users

        result = presence.set_status(user1.id, UserStatus.ONLINE)

        assert result.user_id == user1.id
        assert result.updated_at > 0

    def test_presence_record_created_on_custom_status(self, fresh_users):
        """Test that presence record is created on custom status set."""
        user1, user2, presence = fresh_users

        result = presence.set_custom_status(user1.id, "Status")

        assert result.user_id == user1.id

    def test_presence_record_created_on_activity(self, fresh_users):
        """Test that presence record is created on activity set."""
        user1, user2, presence = fresh_users

        result = presence.set_activity(user1.id, ActivityType.PLAYING, "Game")

        assert result.user_id == user1.id


class TestConcurrentOperations:
    """Tests for concurrent-like operations."""

    def test_rapid_status_changes(self, fresh_users):
        """Test rapid status changes."""
        user1, user2, presence = fresh_users

        statuses = [
            UserStatus.ONLINE,
            UserStatus.IDLE,
            UserStatus.DND,
            UserStatus.INVISIBLE,
            UserStatus.OFFLINE,
            UserStatus.ONLINE,
        ]

        for status in statuses:
            result = presence.set_status(user1.id, status)
            assert result.status == status

    def test_rapid_activity_changes(self, fresh_users):
        """Test rapid activity changes."""
        user1, user2, presence = fresh_users

        activities = [
            ("Game 1", ActivityType.PLAYING),
            ("Stream", ActivityType.STREAMING),
            ("Music", ActivityType.LISTENING),
            ("Video", ActivityType.WATCHING),
            ("Tournament", ActivityType.COMPETING),
        ]

        for name, activity_type in activities:
            result = presence.set_activity(user1.id, activity_type, name)
            assert result.activity.name == name
            assert result.activity.activity_type == activity_type

    def test_rapid_typing_start_stop(self, fresh_users):
        """Test rapid typing start/stop."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        for _ in range(10):
            presence.start_typing(user1.id, channel_id)
            presence.stop_typing(user1.id, channel_id)

        typing_users = presence.get_typing_users(channel_id)
        user_ids = [t.user_id for t in typing_users]
        assert user1.id not in user_ids


class TestTimestamps:
    """Tests for timestamp handling."""

    def test_timestamps_are_milliseconds(self, fresh_users):
        """Test that timestamps are in milliseconds."""
        user1, user2, presence = fresh_users
        import time

        before = int(time.time() * 1000)
        result = presence.set_status(user1.id, UserStatus.ONLINE)
        after = int(time.time() * 1000)

        assert before <= result.last_seen <= after
        assert before <= result.updated_at <= after

    def test_typing_timestamps_milliseconds(self, fresh_users):
        """Test that typing timestamps are in milliseconds."""
        user1, user2, presence = fresh_users
        import time

        before = int(time.time() * 1000)
        indicator = presence.start_typing(user1.id, 12345)
        after = int(time.time() * 1000)

        assert before <= indicator.started_at <= after
        assert indicator.expires_at > indicator.started_at

    def test_custom_status_created_at(self, fresh_users):
        """Test custom status created_at timestamp."""
        user1, user2, presence = fresh_users
        import time

        before = int(time.time() * 1000)
        result = presence.set_custom_status(user1.id, "Status")
        after = int(time.time() * 1000)

        assert before <= result.custom_status.created_at <= after

    def test_activity_created_at(self, fresh_users):
        """Test activity created_at timestamp."""
        user1, user2, presence = fresh_users
        import time

        before = int(time.time() * 1000)
        result = presence.set_activity(user1.id, ActivityType.PLAYING, "Game")
        after = int(time.time() * 1000)

        assert before <= result.activity.created_at <= after


class TestEmptyQueries:
    """Tests for empty query results."""

    def test_get_online_friends_no_relationships_module(self, db_and_modules):
        """Test get_online_friends without relationships module."""
        db, auth, servers, relationships, presence = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]
        user = auth.register(
            username=f"norelmods_{unique_id}",
            email=f"norelmods_{unique_id}@example.com",
            password="TestPass123!"
        )

        # This should work even if no friends
        online = presence.get_online_friends(user.id)
        assert isinstance(online, list)

    def test_get_typing_users_empty_channel(self, fresh_users):
        """Test getting typing users from empty channel."""
        user1, user2, presence = fresh_users

        typing_users = presence.get_typing_users(999999)

        assert len(typing_users) == 0

    def test_get_presences_empty_list(self, fresh_users):
        """Test getting presences with empty user list."""
        user1, user2, presence = fresh_users

        presences = presence.get_presences([])

        assert len(presences) == 0


class TestVisibilityEdgeCases:
    """Tests for visibility edge cases."""

    def test_view_own_invisible_presence(self, fresh_users):
        """Test viewing own presence when invisible."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.INVISIBLE)
        presence.set_activity(user1.id, ActivityType.PLAYING, "Game")

        visible = presence.get_visible_presence(user1.id, user1.id)

        assert visible.status == UserStatus.INVISIBLE
        assert visible.activity is not None

    def test_view_nonexistent_user_presence(self, fresh_users):
        """Test viewing presence of user with no record."""
        user1, user2, presence = fresh_users

        pres = presence.get_presence(user2.id)

        assert pres.status == UserStatus.OFFLINE
        assert pres.custom_status is None
        assert pres.activity is None

    def test_can_see_own_invisible_presence(self, fresh_users):
        """Test can_see_presence for own invisible status."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.INVISIBLE)

        can_see = presence.can_see_presence(user1.id, user1.id)

        assert can_see is True
