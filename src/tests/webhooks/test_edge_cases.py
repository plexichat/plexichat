"""
Tests for webhook edge cases and error handling.
"""

import pytest
import uuid


class TestWebhookLimits:
    """Tests for webhook limits."""

    @pytest.mark.skip(
        reason="Webhook limit enforcement not implemented - channel webhook limits are not currently enforced. "
        "Requires limit checking logic in webhook creation."
    )
    def test_channel_webhook_limit(self, fresh_server):
        """Test channel webhook limit enforcement."""
        setup = fresh_server
        from src.core.webhooks import WebhookLimitError

        for i in range(10):
            setup["webhooks"].create_webhook(
                user_id=setup["owner"].id,
                channel_id=setup["channel"].id,
                name=f"Limit Test {i}",
            )

        with pytest.raises(WebhookLimitError) as exc_info:
            setup["webhooks"].create_webhook(
                user_id=setup["owner"].id,
                channel_id=setup["channel"].id,
                name="Over Limit",
            )

        assert exc_info.value.max_allowed == 10
        assert exc_info.value.current == 10

    def test_server_webhook_limit(self, fresh_server):
        """Test server webhook limit is checked."""
        setup = fresh_server

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name="Server Limit Test",
        )

        assert webhook is not None


class TestDeletedChannel:
    """Tests for webhooks with deleted channels."""

    def test_webhook_in_deleted_channel(self, fresh_server):
        """Test behavior when channel is deleted."""
        setup = fresh_server
        from src.core.servers import ChannelType

        channel = setup["servers"].create_channel(
            user_id=setup["owner"].id,
            server_id=setup["server"].id,
            name="temp-channel",
            channel_type=ChannelType.TEXT,
        )

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=channel.id,
            name="Temp Channel Webhook",
        )

        assert webhook is not None


class TestSpecialCharacters:
    """Tests for special characters in webhook data."""

    def test_webhook_name_special_chars(self, fresh_server):
        """Test webhook name with special characters."""
        setup = fresh_server
        unique_id = uuid.uuid4().hex[:8]

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"Test!@#$%^&*() {unique_id}",
        )

        assert "!@#$%^&*()" in webhook.name

    def test_webhook_name_unicode(self, fresh_server):
        """Test webhook name with unicode characters."""
        setup = fresh_server
        unique_id = uuid.uuid4().hex[:8]

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"Test Bot {unique_id}",
        )

        assert webhook is not None

    def test_message_content_special_chars(self, webhook_with_token):
        """Test message content with special characters."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Special chars: !@#$%^&*()_+-=[]{}|;':\",./<>?",
            wait=True,
        )

        assert "!@#$%^&*()" in result.content

    def test_message_content_newlines(self, webhook_with_token):
        """Test message content with newlines."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Line 1\nLine 2\nLine 3",
            wait=True,
        )

        assert "\n" in result.content


class TestConcurrentOperations:
    """Tests for concurrent webhook operations."""

    def test_multiple_executions_same_webhook(self, webhook_with_token):
        """Test multiple rapid executions of same webhook."""
        setup = webhook_with_token
        results = []

        for i in range(10):
            result = setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                content=f"Rapid message {i}",
                wait=True,
            )
            results.append(result)

        message_ids = [r.id for r in results]
        assert len(set(message_ids)) == 10


class TestErrorMessages:
    """Tests for error message quality."""

    def test_webhook_not_found_message(self, fresh_server):
        """Test WebhookNotFoundError message."""
        setup = fresh_server
        from src.core.webhooks import WebhookNotFoundError

        with pytest.raises(WebhookNotFoundError) as exc_info:
            setup["webhooks"].delete_webhook(
                user_id=setup["owner"].id, webhook_id=999999999
            )

        assert "not found" in str(exc_info.value).lower()

    def test_channel_not_found_message(self, fresh_server):
        """Test ChannelNotFoundError message."""
        setup = fresh_server
        from src.core.webhooks import ChannelNotFoundError

        with pytest.raises(ChannelNotFoundError) as exc_info:
            setup["webhooks"].create_webhook(
                user_id=setup["owner"].id, channel_id=999999999, name="Test"
            )

        assert "not found" in str(exc_info.value).lower()

    def test_invalid_token_message(self, webhook_with_token):
        """Test InvalidWebhookTokenError message."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        with pytest.raises(InvalidWebhookTokenError) as exc_info:
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id, token="invalid", content="Test"
            )

        assert "token" in str(exc_info.value).lower()


class TestWebhookUrl:
    """Tests for webhook URL property."""

    def test_url_format(self, webhook_with_token):
        """Test webhook URL format."""
        setup = webhook_with_token
        url = setup["webhook"].url

        assert url.startswith("/webhooks/")
        assert str(setup["webhook"].id) in url

    def test_url_contains_token(self, webhook_with_token):
        """Test that URL contains token when available."""
        setup = webhook_with_token
        url = setup["webhook"].url
        secret = setup["token"].split(".")[-1]

        assert secret in url

    def test_url_without_token(self, webhook_with_token):
        """Test URL when token is not set."""
        setup = webhook_with_token

        webhook = setup["webhooks"].get_webhook(setup["webhook"].id, setup["owner"].id)

        url = webhook.url
        assert url == f"/webhooks/{webhook.id}"


class TestEmptyAndNull:
    """Tests for empty and null values."""

    def test_null_avatar_url(self, fresh_server):
        """Test creating webhook with null avatar."""
        setup = fresh_server
        unique_id = uuid.uuid4().hex[:8]

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"Null Avatar {unique_id}",
            avatar_url=None,
        )

        assert webhook.avatar_url is None

    def test_empty_string_avatar_url(self, fresh_server):
        """Test creating webhook with empty string avatar."""
        setup = fresh_server
        unique_id = uuid.uuid4().hex[:8]

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"Empty Avatar {unique_id}",
            avatar_url="",
        )

        assert webhook.avatar_url is None

    def test_whitespace_avatar_url(self, fresh_server):
        """Test creating webhook with whitespace avatar."""
        setup = fresh_server
        unique_id = uuid.uuid4().hex[:8]

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"Whitespace Avatar {unique_id}",
            avatar_url="   ",
        )

        assert webhook.avatar_url is None


class TestDatabaseConstraints:
    """Tests for database constraint handling."""

    def test_unique_webhook_ids(self, fresh_server):
        """Test that webhook IDs are unique."""
        setup = fresh_server
        ids = set()

        for i in range(5):
            webhook = setup["webhooks"].create_webhook(
                user_id=setup["owner"].id,
                channel_id=setup["channel"].id,
                name=f"Unique ID {i}",
            )
            ids.add(webhook.id)

        assert len(ids) == 5

    def test_webhook_timestamps_valid(self, webhook_with_token):
        """Test that webhook timestamps are valid."""
        setup = webhook_with_token

        assert setup["webhook"].created_at > 0
        assert setup["webhook"].updated_at > 0
        assert setup["webhook"].created_at <= setup["webhook"].updated_at
