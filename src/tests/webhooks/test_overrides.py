"""
Tests for webhook username and avatar overrides per message.
"""

import pytest
import uuid


class TestUsernameOverride:
    """Tests for username override on webhook messages."""

    def test_username_override(self, webhook_with_token):
        """Test overriding webhook username for a message."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Custom username message",
            username="Custom Bot Name",
            wait=True,
        )

        assert result.username == "Custom Bot Name"

    def test_username_override_default(self, webhook_with_token):
        """Test that default username is webhook name."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Default username message",
            wait=True,
        )

        assert result.username == setup["webhook"].name

    def test_username_override_max_length(self, webhook_with_token):
        """Test username override at max length."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Max length username",
            username="x" * 80,
            wait=True,
        )

        assert len(result.username) == 80

    def test_username_override_too_long(self, webhook_with_token):
        """Test username override exceeding max length."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidContentError

        with pytest.raises(InvalidContentError) as exc_info:
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                content="Too long username",
                username="x" * 81,
            )

        assert "username_too_long" in exc_info.value.issues

    def test_username_override_sanitized(self, webhook_with_token):
        """Test that username override is sanitized."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Sanitized username",
            username="Test <script>alert(1)</script> Bot",
            wait=True,
        )

        assert "<script>" not in result.username

    def test_username_override_whitespace_trimmed(self, webhook_with_token):
        """Test that username whitespace is trimmed."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Trimmed username",
            username="  Trimmed Name  ",
            wait=True,
        )

        assert result.username == "Trimmed Name"

    def test_username_override_empty_uses_default(self, webhook_with_token):
        """Test that empty username uses webhook default."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Empty username",
            username="",
            wait=True,
        )

        assert result.username == setup["webhook"].name


class TestAvatarOverride:
    """Tests for avatar URL override on webhook messages."""

    def test_avatar_override(self, webhook_with_token):
        """Test overriding webhook avatar for a message."""
        setup = webhook_with_token
        custom_avatar = "https://example.com/custom-avatar.png"

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Custom avatar message",
            avatar_url=custom_avatar,
            wait=True,
        )

        assert result.avatar_url == custom_avatar

    def test_avatar_override_default(self, base_server_setup):
        """Test that default avatar is webhook avatar."""
        setup = base_server_setup
        unique_id = uuid.uuid4().hex[:8]
        webhook_avatar = "https://example.com/webhook-avatar.png"

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"Avatar Default {unique_id}",
            avatar_url=webhook_avatar,
        )

        result = setup["webhooks"].execute_webhook(
            webhook_id=webhook.id,
            token=webhook.token,
            content="Default avatar message",
            wait=True,
        )

        assert result.avatar_url == webhook_avatar

    def test_avatar_override_none_default(self, webhook_with_token):
        """Test default avatar when webhook has none."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="No avatar message",
            wait=True,
        )

        assert result.avatar_url == setup["webhook"].avatar_url

    def test_avatar_override_http(self, webhook_with_token):
        """Test HTTP avatar URL override."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="HTTP avatar",
            avatar_url="http://example.com/avatar.png",
            wait=True,
        )

        assert result.avatar_url == "http://example.com/avatar.png"

    def test_avatar_override_https(self, webhook_with_token):
        """Test HTTPS avatar URL override."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="HTTPS avatar",
            avatar_url="https://example.com/avatar.png",
            wait=True,
        )

        assert result.avatar_url == "https://example.com/avatar.png"

    def test_avatar_override_invalid_scheme(self, webhook_with_token):
        """Test invalid avatar URL scheme."""
        setup = webhook_with_token
        from src.core.webhooks import WebhookAvatarError

        with pytest.raises(WebhookAvatarError):
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                content="Invalid avatar",
                avatar_url="javascript:alert(1)",
            )

    def test_avatar_override_data_uri(self, webhook_with_token):
        """Test data URI avatar URL."""
        setup = webhook_with_token
        from src.core.webhooks import WebhookAvatarError

        with pytest.raises(WebhookAvatarError):
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                content="Data URI avatar",
                avatar_url="data:image/png;base64,abc123",
            )


class TestCombinedOverrides:
    """Tests for combined username and avatar overrides."""

    def test_both_overrides(self, webhook_with_token):
        """Test both username and avatar override."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Both overrides",
            username="Custom Name",
            avatar_url="https://example.com/custom.png",
            wait=True,
        )

        assert result.username == "Custom Name"
        assert result.avatar_url == "https://example.com/custom.png"

    def test_different_overrides_per_message(self, webhook_with_token):
        """Test different overrides for different messages."""
        setup = webhook_with_token

        result1 = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Message 1",
            username="Bot One",
            wait=True,
        )

        result2 = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Message 2",
            username="Bot Two",
            wait=True,
        )

        assert result1.username == "Bot One"
        assert result2.username == "Bot Two"

    def test_override_does_not_change_webhook(self, webhook_with_token):
        """Test that overrides don't change webhook settings."""
        setup = webhook_with_token
        original_name = setup["webhook"].name

        setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Override test",
            username="Temporary Name",
            wait=True,
        )

        webhook = setup["webhooks"].get_webhook(setup["webhook"].id, setup["owner"].id)

        assert webhook.name == original_name
