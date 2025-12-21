"""
Tests for typing indicators.
"""

import time


class TestStartTyping:
    """Tests for starting typing indicator."""

    def test_start_typing_success(self, fresh_users):
        """Test starting typing indicator."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        indicator = presence.start_typing(user1.id, channel_id)

        assert indicator.user_id == user1.id
        assert indicator.channel_id == channel_id
        assert indicator.started_at > 0
        assert indicator.expires_at > indicator.started_at

    def test_start_typing_sets_expiration(self, fresh_users):
        """Test that typing indicator has expiration."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        indicator = presence.start_typing(user1.id, channel_id)

        # Should expire in ~10 seconds
        expected_timeout = 10000  # 10 seconds in ms
        actual_timeout = indicator.expires_at - indicator.started_at

        assert actual_timeout == expected_timeout

    def test_start_typing_multiple_channels(self, fresh_users):
        """Test typing in multiple channels."""
        user1, user2, presence = fresh_users
        channel1 = 12345
        channel2 = 67890

        indicator1 = presence.start_typing(user1.id, channel1)
        indicator2 = presence.start_typing(user1.id, channel2)

        assert indicator1.channel_id == channel1
        assert indicator2.channel_id == channel2

    def test_start_typing_refreshes_indicator(self, fresh_users):
        """Test that starting typing again refreshes the indicator."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        indicator1 = presence.start_typing(user1.id, channel_id)
        time.sleep(0.01)  # Small delay
        indicator2 = presence.start_typing(user1.id, channel_id)

        # Second indicator should have later timestamps
        assert indicator2.started_at >= indicator1.started_at


class TestStopTyping:
    """Tests for stopping typing indicator."""

    def test_stop_typing_success(self, fresh_users):
        """Test stopping typing indicator."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        presence.start_typing(user1.id, channel_id)
        result = presence.stop_typing(user1.id, channel_id)

        assert result is True

    def test_stop_typing_removes_indicator(self, fresh_users):
        """Test that stopping removes the indicator."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        presence.start_typing(user1.id, channel_id)
        presence.stop_typing(user1.id, channel_id)

        typing_users = presence.get_typing_users(channel_id)
        user_ids = [t.user_id for t in typing_users]

        assert user1.id not in user_ids

    def test_stop_typing_when_not_typing(self, fresh_users):
        """Test stopping when not typing."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        result = presence.stop_typing(user1.id, channel_id)

        assert result is True

    def test_stop_typing_one_channel_only(self, fresh_users):
        """Test that stopping only affects one channel."""
        user1, user2, presence = fresh_users
        channel1 = 12345
        channel2 = 67890

        presence.start_typing(user1.id, channel1)
        presence.start_typing(user1.id, channel2)
        presence.stop_typing(user1.id, channel1)

        typing1 = presence.get_typing_users(channel1)
        typing2 = presence.get_typing_users(channel2)

        assert len([t for t in typing1 if t.user_id == user1.id]) == 0
        assert len([t for t in typing2 if t.user_id == user1.id]) == 1


class TestGetTypingUsers:
    """Tests for getting typing users."""

    def test_get_typing_users_success(self, fresh_users):
        """Test getting typing users in a channel."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        presence.start_typing(user1.id, channel_id)
        presence.start_typing(user2.id, channel_id)

        typing_users = presence.get_typing_users(channel_id)

        user_ids = [t.user_id for t in typing_users]
        assert user1.id in user_ids
        assert user2.id in user_ids

    def test_get_typing_users_empty(self, fresh_users):
        """Test getting typing users when none typing."""
        user1, user2, presence = fresh_users
        channel_id = 99999

        typing_users = presence.get_typing_users(channel_id)

        assert len(typing_users) == 0

    def test_get_typing_users_different_channels(self, fresh_users):
        """Test that typing users are channel-specific."""
        user1, user2, presence = fresh_users
        import random
        channel1 = random.randint(1000000, 9999999)
        channel2 = random.randint(10000000, 99999999)

        presence.start_typing(user1.id, channel1)
        presence.start_typing(user2.id, channel2)

        typing1 = presence.get_typing_users(channel1)
        typing2 = presence.get_typing_users(channel2)

        assert len(typing1) == 1
        assert typing1[0].user_id == user1.id
        assert len(typing2) == 1
        assert typing2[0].user_id == user2.id


class TestTypingExpiration:
    """Tests for typing indicator expiration."""

    def test_typing_indicator_has_expiration(self, fresh_users):
        """Test that typing indicator has expiration time."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        indicator = presence.start_typing(user1.id, channel_id)

        assert indicator.expires_at > indicator.started_at

    def test_typing_indicator_timeout_value(self, fresh_users):
        """Test typing indicator timeout is 10 seconds."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        indicator = presence.start_typing(user1.id, channel_id)
        timeout = indicator.expires_at - indicator.started_at

        assert timeout == 10000  # 10 seconds in milliseconds


class TestTypingMultipleUsers:
    """Tests for multiple users typing."""

    def test_multiple_users_same_channel(self, db_and_modules):
        """Test multiple users typing in same channel."""
        db, auth, servers, relationships, presence = db_and_modules
        import uuid
        import random

        unique_id = uuid.uuid4().hex[:6]
        channel_id = random.randint(100000000, 999999999)

        users = []
        for i in range(5):
            user = auth.register(
                username=f"typer_{unique_id}_{i}",
                email=f"typer_{unique_id}_{i}@example.com",
                password="TestPass123!"
            )
            users.append(user)
            presence.start_typing(user.id, channel_id)

        typing_users = presence.get_typing_users(channel_id)

        assert len(typing_users) == 5

    def test_user_typing_multiple_channels(self, fresh_users):
        """Test user typing in multiple channels."""
        user1, user2, presence = fresh_users
        channels = [11111, 22222, 33333]

        for channel_id in channels:
            presence.start_typing(user1.id, channel_id)

        for channel_id in channels:
            typing_users = presence.get_typing_users(channel_id)
            user_ids = [t.user_id for t in typing_users]
            assert user1.id in user_ids

    def test_stop_typing_one_user_keeps_others(self, fresh_users):
        """Test that stopping one user keeps others typing."""
        user1, user2, presence = fresh_users
        channel_id = 12345

        presence.start_typing(user1.id, channel_id)
        presence.start_typing(user2.id, channel_id)
        presence.stop_typing(user1.id, channel_id)

        typing_users = presence.get_typing_users(channel_id)
        user_ids = [t.user_id for t in typing_users]

        assert user1.id not in user_ids
        assert user2.id in user_ids
