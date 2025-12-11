"""
Tests for webhook execution (sending messages).
"""

import pytest


class TestExecuteWebhook:
    """Tests for executing webhooks to send messages."""

    def test_execute_webhook_basic(self, webhook_with_token):
        """Test basic webhook execution."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Hello from webhook!",
            wait=True
        )

        assert result is not None
        assert result.webhook_id == setup["webhook"].id
        assert result.content == "Hello from webhook!"

    def test_execute_webhook_no_wait(self, webhook_with_token):
        """Test webhook execution without waiting."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="No wait message"
        )

        assert result is None

    def test_execute_webhook_with_full_token(self, webhook_with_token):
        """Test execution with full token format."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Full token test",
            wait=True
        )

        assert result is not None
        assert result.content == "Full token test"

    def test_execute_webhook_with_secret_only(self, webhook_with_token):
        """Test execution with secret part of token only."""
        setup = webhook_with_token
        secret = setup["token"].split(".")[-1]

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=secret,
            content="Secret only test",
            wait=True
        )

        assert result is not None
        assert result.content == "Secret only test"

    def test_execute_webhook_invalid_token(self, webhook_with_token):
        """Test execution with invalid token."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token="invalid_token_here",
                content="Should fail"
            )

    def test_execute_webhook_wrong_webhook_id(self, webhook_with_token):
        """Test execution with mismatched webhook ID."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].execute_webhook(
                webhook_id=999999999,
                token=setup["token"],
                content="Wrong ID"
            )

    def test_execute_webhook_empty_content(self, webhook_with_token):
        """Test execution with empty content and no embeds."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidContentError

        with pytest.raises(InvalidContentError):
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                content=""
            )

    def test_execute_webhook_no_content_no_embeds(self, webhook_with_token):
        """Test execution with neither content nor embeds."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidContentError

        with pytest.raises(InvalidContentError):
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"]
            )

    def test_execute_webhook_content_too_long(self, webhook_with_token):
        """Test execution with content exceeding max length."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidContentError

        with pytest.raises(InvalidContentError) as exc_info:
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                content="x" * 3000
            )

        assert "content_too_long" in exc_info.value.issues

    def test_execute_webhook_message_id_generated(self, webhook_with_token):
        """Test that executed message gets a unique ID."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="ID test",
            wait=True
        )

        assert result.id > 0

    def test_execute_webhook_channel_id_set(self, webhook_with_token):
        """Test that message has correct channel ID."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Channel test",
            wait=True
        )

        assert result.channel_id == setup["webhook"].channel_id

    def test_execute_webhook_timestamp_set(self, webhook_with_token):
        """Test that message has timestamp."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Timestamp test",
            wait=True
        )

        assert result.created_at > 0

    def test_execute_webhook_multiple_messages(self, webhook_with_token):
        """Test sending multiple messages via webhook."""
        setup = webhook_with_token
        message_ids = set()

        for i in range(5):
            result = setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                content=f"Message {i}",
                wait=True
            )
            message_ids.add(result.id)

        assert len(message_ids) == 5


class TestExecuteWebhookByUrl:
    """Tests for executing webhooks by URL."""

    def test_execute_by_url(self, webhook_with_token):
        """Test executing webhook by URL."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook_by_url(
            webhook_url=setup["webhook"].url,
            content="URL execution test",
            wait=True
        )

        assert result is not None
        assert result.content == "URL execution test"

    def test_execute_by_url_with_leading_slash(self, webhook_with_token):
        """Test URL with leading slash."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook_by_url(
            webhook_url=f"/webhooks/{setup['webhook'].id}/{setup['token'].split('.')[-1]}",
            content="Leading slash test",
            wait=True
        )

        assert result is not None

    def test_execute_by_url_without_leading_slash(self, webhook_with_token):
        """Test URL without leading slash."""
        setup = webhook_with_token
        url = setup["webhook"].url.lstrip("/")

        result = setup["webhooks"].execute_webhook_by_url(
            webhook_url=url,
            content="No slash test",
            wait=True
        )

        assert result is not None

    def test_execute_by_url_invalid_format(self, webhook_with_token):
        """Test execution with invalid URL format."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].execute_webhook_by_url(
                webhook_url="invalid/url/format",
                content="Should fail"
            )

    def test_execute_by_url_missing_token(self, webhook_with_token):
        """Test execution with URL missing token."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].execute_webhook_by_url(
                webhook_url=f"/webhooks/{setup['webhook'].id}",
                content="Should fail"
            )


class TestGetWebhookByToken:
    """Tests for getting webhook by token."""

    def test_get_webhook_by_token(self, webhook_with_token):
        """Test getting webhook by its token."""
        setup = webhook_with_token

        webhook = setup["webhooks"].get_webhook_by_token(setup["token"])

        assert webhook is not None
        assert webhook.id == setup["webhook"].id
        assert webhook.name == setup["webhook"].name

    def test_get_webhook_by_token_invalid(self, webhook_with_token):
        """Test getting webhook with invalid token."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].get_webhook_by_token("webhook.123.invalid")

    def test_get_webhook_by_token_wrong_format(self, webhook_with_token):
        """Test getting webhook with wrong token format."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].get_webhook_by_token("not_a_valid_token")

    def test_get_webhook_by_token_nonexistent(self, webhook_with_token):
        """Test getting non-existent webhook by token."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].get_webhook_by_token("webhook.999999999.abcdef123456")
