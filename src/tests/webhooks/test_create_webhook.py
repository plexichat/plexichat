"""
Tests for webhook creation and token generation.
"""

import pytest
import uuid


class TestCreateWebhook:
    """Tests for creating webhooks."""

    def test_create_webhook_basic(self, base_server_setup):
        """Test creating a basic webhook."""
        setup = base_server_setup
        unique_id = uuid.uuid4().hex[:8]

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"Basic Webhook {unique_id}",
        )

        assert webhook is not None
        assert webhook.id > 0
        assert webhook.channel_id == setup["channel"].id
        assert webhook.server_id == setup["server"].id
        assert webhook.creator_id == setup["owner"].id
        assert webhook.name == f"Basic Webhook {unique_id}"
        assert webhook.token is not None
        assert webhook.avatar_url is None

    def test_create_webhook_with_avatar(self, base_server_setup):
        """Test creating a webhook with avatar URL."""
        setup = base_server_setup
        unique_id = uuid.uuid4().hex[:8]
        avatar = "https://example.com/avatar.png"

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"Avatar Webhook {unique_id}",
            avatar_url=avatar,
        )

        assert webhook.avatar_url == avatar

    def test_create_webhook_token_format(self, base_server_setup):
        """Test that webhook token has correct format."""
        setup = base_server_setup
        unique_id = uuid.uuid4().hex[:8]

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"Token Format {unique_id}",
        )

        assert webhook.token.startswith("webhook.")
        parts = webhook.token.split(".")
        assert len(parts) == 3
        assert parts[0] == "webhook"
        assert parts[1] == str(webhook.id)
        assert len(parts[2]) > 20

    def test_create_webhook_url_property(self, base_server_setup):
        """Test webhook URL property."""
        setup = base_server_setup
        unique_id = uuid.uuid4().hex[:8]

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name=f"URL Test {unique_id}",
        )

        assert f"/webhooks/{webhook.id}/" in webhook.url
        assert webhook.token.split(".")[-1] in webhook.url

    def test_create_webhook_unique_tokens(self, base_server_setup):
        """Test that each webhook gets a unique token."""
        setup = base_server_setup
        tokens = set()

        for i in range(5):
            webhook = setup["webhooks"].create_webhook(
                user_id=setup["owner"].id,
                channel_id=setup["channel"].id,
                name=f"Unique Token {i}",
            )
            tokens.add(webhook.token)

        assert len(tokens) == 5

    def test_create_webhook_invalid_channel(self, base_server_setup):
        """Test creating webhook for non-existent channel."""
        setup = base_server_setup
        from src.core.webhooks import ChannelNotFoundError

        with pytest.raises(ChannelNotFoundError):
            setup["webhooks"].create_webhook(
                user_id=setup["owner"].id, channel_id=999999999, name="Invalid Channel"
            )

    def test_create_webhook_no_permission(self, base_server_setup):
        """Test creating webhook without permission."""
        setup = base_server_setup
        from src.core.webhooks import PermissionDeniedError

        with pytest.raises(PermissionDeniedError) as exc_info:
            setup["webhooks"].create_webhook(
                user_id=setup["non_member"].id,
                channel_id=setup["channel"].id,
                name="No Permission",
            )

        assert exc_info.value.permission == "webhooks.manage"

    def test_create_webhook_empty_name(self, base_server_setup):
        """Test creating webhook with empty name."""
        setup = base_server_setup
        from src.core.webhooks import WebhookNameError

        with pytest.raises(WebhookNameError):
            setup["webhooks"].create_webhook(
                user_id=setup["owner"].id, channel_id=setup["channel"].id, name=""
            )

    def test_create_webhook_whitespace_name(self, base_server_setup):
        """Test creating webhook with whitespace-only name."""
        setup = base_server_setup
        from src.core.webhooks import WebhookNameError

        with pytest.raises(WebhookNameError):
            setup["webhooks"].create_webhook(
                user_id=setup["owner"].id, channel_id=setup["channel"].id, name="   "
            )

    def test_create_webhook_name_too_long(self, base_server_setup):
        """Test creating webhook with name exceeding max length."""
        setup = base_server_setup
        from src.core.webhooks import WebhookNameError

        with pytest.raises(WebhookNameError) as exc_info:
            setup["webhooks"].create_webhook(
                user_id=setup["owner"].id, channel_id=setup["channel"].id, name="x" * 81
            )

        assert exc_info.value.max_length == 80

    def test_create_webhook_name_max_length(self, base_server_setup):
        """Test creating webhook with name at max length."""
        setup = base_server_setup

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id, channel_id=setup["channel"].id, name="x" * 80
        )

        assert len(webhook.name) == 80

    def test_create_webhook_name_sanitized(self, fresh_server):
        """Test that webhook name is sanitized (HTML tags removed)."""
        setup = fresh_server

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name="Test <script>alert(1)</script> Webhook",
        )

        assert "<script>" not in webhook.name
        assert "</script>" not in webhook.name

    def test_create_webhook_invalid_avatar_scheme(self, base_server_setup):
        """Test creating webhook with invalid avatar URL scheme."""
        setup = base_server_setup
        from src.core.webhooks import WebhookAvatarError

        with pytest.raises(WebhookAvatarError):
            setup["webhooks"].create_webhook(
                user_id=setup["owner"].id,
                channel_id=setup["channel"].id,
                name="Invalid Avatar",
                avatar_url="javascript:alert(1)",
            )

    def test_create_webhook_invalid_avatar_data_uri(self, base_server_setup):
        """Test creating webhook with data URI avatar."""
        setup = base_server_setup
        from src.core.webhooks import WebhookAvatarError

        with pytest.raises(WebhookAvatarError):
            setup["webhooks"].create_webhook(
                user_id=setup["owner"].id,
                channel_id=setup["channel"].id,
                name="Data URI Avatar",
                avatar_url="data:image/png;base64,abc123",
            )

    def test_create_webhook_http_avatar(self, fresh_server):
        """Test creating webhook with HTTP avatar URL."""
        setup = fresh_server

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name="HTTP Avatar Test",
            avatar_url="http://example.com/avatar.png",
        )

        assert webhook.avatar_url == "http://example.com/avatar.png"

    def test_create_webhook_https_avatar(self, fresh_server):
        """Test creating webhook with HTTPS avatar URL."""
        setup = fresh_server

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name="HTTPS Avatar Test",
            avatar_url="https://example.com/avatar.png",
        )

        assert webhook.avatar_url == "https://example.com/avatar.png"

    def test_create_webhook_timestamps(self, fresh_server):
        """Test that webhook has valid timestamps."""
        setup = fresh_server

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name="Timestamp Test",
        )

        assert webhook.created_at > 0
        assert webhook.updated_at > 0
        assert webhook.created_at == webhook.updated_at

    def test_create_webhook_type_incoming(self, fresh_server):
        """Test that created webhook has INCOMING type."""
        setup = fresh_server
        from src.core.webhooks import WebhookType

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id, channel_id=setup["channel"].id, name="Type Test"
        )

        assert webhook.webhook_type == WebhookType.INCOMING
