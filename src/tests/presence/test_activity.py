"""
Tests for activity tracking.
"""

import pytest
from src.core.presence import (
    ActivityType,
    InvalidActivityError,
)


class TestSetActivity:
    """Tests for setting user activity."""

    def test_set_activity_playing(self, fresh_users):
        """Test setting playing activity."""
        user1, user2, presence = fresh_users

        result = presence.set_activity(
            user1.id,
            ActivityType.PLAYING,
            "Minecraft"
        )

        assert result.activity is not None
        assert result.activity.activity_type == ActivityType.PLAYING
        assert result.activity.name == "Minecraft"

    def test_set_activity_streaming(self, fresh_users):
        """Test setting streaming activity."""
        user1, user2, presence = fresh_users

        result = presence.set_activity(
            user1.id,
            ActivityType.STREAMING,
            "Live Coding",
            url="https://twitch.tv/example"
        )

        assert result.activity.activity_type == ActivityType.STREAMING
        assert result.activity.url == "https://twitch.tv/example"

    def test_set_activity_listening(self, fresh_users):
        """Test setting listening activity."""
        user1, user2, presence = fresh_users

        result = presence.set_activity(
            user1.id,
            ActivityType.LISTENING,
            "Spotify",
            details="Song Title",
            state="by Artist"
        )

        assert result.activity.activity_type == ActivityType.LISTENING
        assert result.activity.details == "Song Title"
        assert result.activity.state == "by Artist"

    def test_set_activity_watching(self, fresh_users):
        """Test setting watching activity."""
        user1, user2, presence = fresh_users

        result = presence.set_activity(
            user1.id,
            ActivityType.WATCHING,
            "YouTube"
        )

        assert result.activity.activity_type == ActivityType.WATCHING

    def test_set_activity_competing(self, fresh_users):
        """Test setting competing activity."""
        user1, user2, presence = fresh_users

        result = presence.set_activity(
            user1.id,
            ActivityType.COMPETING,
            "Tournament"
        )

        assert result.activity.activity_type == ActivityType.COMPETING

    def test_set_activity_custom(self, fresh_users):
        """Test setting custom activity."""
        user1, user2, presence = fresh_users

        result = presence.set_activity(
            user1.id,
            ActivityType.CUSTOM,
            "Custom Activity"
        )

        assert result.activity.activity_type == ActivityType.CUSTOM

    def test_set_activity_with_timestamps(self, fresh_users):
        """Test setting activity with timestamps."""
        user1, user2, presence = fresh_users
        import time
        start = int(time.time() * 1000)

        result = presence.set_activity(
            user1.id,
            ActivityType.PLAYING,
            "Game",
            timestamps={"start": start}
        )

        assert result.activity.start_timestamp == start

    def test_set_activity_with_end_timestamp(self, fresh_users):
        """Test setting activity with end timestamp."""
        user1, user2, presence = fresh_users
        import time
        start = int(time.time() * 1000)
        end = start + 3600000

        result = presence.set_activity(
            user1.id,
            ActivityType.PLAYING,
            "Game",
            timestamps={"start": start, "end": end}
        )

        assert result.activity.start_timestamp == start
        assert result.activity.end_timestamp == end

    def test_set_activity_with_assets(self, fresh_users):
        """Test setting activity with image assets."""
        user1, user2, presence = fresh_users

        result = presence.set_activity(
            user1.id,
            ActivityType.PLAYING,
            "Game",
            assets={
                "large_image": "game_logo",
                "large_text": "Game Name",
                "small_image": "rank_icon",
                "small_text": "Diamond Rank"
            }
        )

        assert result.activity.large_image == "game_logo"
        assert result.activity.large_text == "Game Name"
        assert result.activity.small_image == "rank_icon"
        assert result.activity.small_text == "Diamond Rank"

    def test_set_activity_empty_name_fails(self, fresh_users):
        """Test that empty activity name fails."""
        user1, user2, presence = fresh_users

        with pytest.raises(InvalidActivityError):
            presence.set_activity(user1.id, ActivityType.PLAYING, "")

    def test_set_activity_whitespace_name_fails(self, fresh_users):
        """Test that whitespace-only activity name fails."""
        user1, user2, presence = fresh_users

        with pytest.raises(InvalidActivityError):
            presence.set_activity(user1.id, ActivityType.PLAYING, "   ")


class TestGetActivity:
    """Tests for getting user activity."""

    def test_get_activity_after_set(self, fresh_users):
        """Test getting activity after setting it."""
        user1, user2, presence = fresh_users

        presence.set_activity(user1.id, ActivityType.PLAYING, "Game")
        activity = presence.get_activity(user1.id)

        assert activity is not None
        assert activity.name == "Game"

    def test_get_activity_none(self, fresh_users):
        """Test getting activity when none set."""
        user1, user2, presence = fresh_users

        activity = presence.get_activity(user2.id)

        assert activity is None

    def test_get_activity_created_at(self, fresh_users):
        """Test that activity has created_at timestamp."""
        user1, user2, presence = fresh_users

        presence.set_activity(user1.id, ActivityType.PLAYING, "Game")
        activity = presence.get_activity(user1.id)

        assert activity.created_at > 0


class TestClearActivity:
    """Tests for clearing user activity."""

    def test_clear_activity(self, fresh_users):
        """Test clearing activity."""
        user1, user2, presence = fresh_users

        presence.set_activity(user1.id, ActivityType.PLAYING, "Game")
        result = presence.clear_activity(user1.id)

        assert result.activity is None

    def test_clear_activity_when_none(self, fresh_users):
        """Test clearing activity when none set."""
        user1, user2, presence = fresh_users

        result = presence.clear_activity(user1.id)

        assert result.activity is None


class TestActivityUpdate:
    """Tests for updating activity."""

    def test_update_activity_replaces(self, fresh_users):
        """Test that setting new activity replaces old one."""
        user1, user2, presence = fresh_users

        presence.set_activity(user1.id, ActivityType.PLAYING, "Game 1")
        result = presence.set_activity(user1.id, ActivityType.PLAYING, "Game 2")

        assert result.activity.name == "Game 2"

    def test_update_activity_type(self, fresh_users):
        """Test changing activity type."""
        user1, user2, presence = fresh_users

        presence.set_activity(user1.id, ActivityType.PLAYING, "Game")
        result = presence.set_activity(user1.id, ActivityType.STREAMING, "Stream")

        assert result.activity.activity_type == ActivityType.STREAMING
        assert result.activity.name == "Stream"


class TestActivityWithPresence:
    """Tests for activity with full presence."""

    def test_activity_in_presence(self, fresh_users):
        """Test that activity is included in presence."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, presence.UserStatus.ONLINE)
        presence.set_activity(user1.id, ActivityType.PLAYING, "Game")

        pres = presence.get_presence(user1.id)

        assert pres.activity is not None
        assert pres.activity.name == "Game"

    def test_activity_independent_of_status(self, fresh_users):
        """Test that activity persists across status changes."""
        user1, user2, presence = fresh_users

        presence.set_activity(user1.id, ActivityType.PLAYING, "Game")
        presence.set_status(user1.id, presence.UserStatus.ONLINE)
        presence.set_status(user1.id, presence.UserStatus.IDLE)

        pres = presence.get_presence(user1.id)

        assert pres.activity is not None
        assert pres.activity.name == "Game"

    def test_clear_activity_keeps_status(self, fresh_users):
        """Test that clearing activity keeps status."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, presence.UserStatus.ONLINE)
        presence.set_activity(user1.id, ActivityType.PLAYING, "Game")
        presence.clear_activity(user1.id)

        pres = presence.get_presence(user1.id)

        assert pres.status == presence.UserStatus.ONLINE
        assert pres.activity is None
