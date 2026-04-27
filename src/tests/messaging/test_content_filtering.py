"""Tests for messaging content filtering."""

import pytest


@pytest.mark.messaging
class TestContentFiltering:
    """Tests for content filter settings and enforcement."""

    def test_get_default_filter_settings(self, messaging_manager, test_user):
        """Test getting default content filter settings for a user."""
        settings = messaging_manager.get_user_filter_settings(test_user.id)
        assert settings is not None

    def test_update_profanity_filter(self, messaging_manager, test_user):
        """Test enabling/disabling profanity filter."""
        settings = messaging_manager.update_user_filter_settings(
            test_user.id, profanity_filter=True
        )
        assert settings.profanity_filter is True

    def test_update_nsfw_filter(self, messaging_manager, test_user):
        """Test enabling/disabling NSFW filter."""
        settings = messaging_manager.update_user_filter_settings(
            test_user.id, nsfw_filter=True
        )
        assert settings.nsfw_filter is True

    def test_update_spoiler_settings(self, messaging_manager, test_user):
        """Test updating spoiler click-to-reveal settings."""
        settings = messaging_manager.update_user_filter_settings(
            test_user.id, spoiler_click_to_reveal=True
        )
        assert settings.spoiler_click_to_reveal is True

    def test_update_custom_blocked_words(self, messaging_manager, test_user):
        """Test adding custom blocked words."""
        custom_words = ["badword1", "badword2"]
        settings = messaging_manager.update_user_filter_settings(
            test_user.id, custom_blocked_words=custom_words
        )
        assert settings.custom_blocked_words == custom_words

    def test_get_user_message_settings(self, messaging_manager, test_user):
        """Test getting user message settings."""
        settings = messaging_manager.get_user_message_settings(test_user.id)
        assert settings is not None

    def test_update_message_settings(self, messaging_manager, test_user):
        """Test updating user message settings."""
        settings = messaging_manager.update_user_message_settings(
            test_user.id, read_receipts_enabled=False
        )
        assert settings.read_receipts_enabled is False

    def test_update_typing_indicators(self, messaging_manager, test_user):
        """Test updating typing indicator settings."""
        settings = messaging_manager.update_user_message_settings(
            test_user.id, typing_indicators_enabled=False
        )
        assert settings.typing_indicators_enabled is False
