"""
Tests for webhook integration with messaging and embeds modules.
"""



class TestMessagingIntegration:
    """Tests for webhook integration with messaging module."""

    def test_webhook_message_stored(self, webhook_with_token):
        """Test that webhook messages are stored in database."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Stored message",
            wait=True
        )

        row = setup["db"].fetch_one(
            "SELECT * FROM webhook_messages WHERE message_id = ?",
            (result.id,)
        )

        assert row is not None
        assert row["webhook_id"] == setup["webhook"].id

    def test_webhook_message_tracking(self, webhook_with_token):
        """Test webhook message tracking records."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Tracked message",
            username="Custom Name",
            avatar_url="https://example.com/avatar.png",
            wait=True
        )

        row = setup["db"].fetch_one(
            "SELECT * FROM webhook_messages WHERE message_id = ?",
            (result.id,)
        )

        assert row["username_override"] == "Custom Name"
        assert row["avatar_override"] == "https://example.com/avatar.png"

    def test_webhook_message_channel_recorded(self, webhook_with_token):
        """Test that channel ID is recorded for webhook messages."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Channel recorded",
            wait=True
        )

        row = setup["db"].fetch_one(
            "SELECT * FROM webhook_messages WHERE message_id = ?",
            (result.id,)
        )

        assert row["channel_id"] == setup["webhook"].channel_id

    def test_webhook_message_thread_recorded(self, webhook_with_token):
        """Test that thread ID is recorded for webhook messages."""
        setup = webhook_with_token
        thread_id = 123456789

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Thread recorded",
            thread_id=thread_id,
            wait=True
        )

        row = setup["db"].fetch_one(
            "SELECT * FROM webhook_messages WHERE message_id = ?",
            (result.id,)
        )

        assert row["thread_id"] == thread_id


class TestEmbedIntegration:
    """Tests for webhook integration with embeds module."""

    def test_webhook_embed_creation(self, webhook_with_token):
        """Test that webhook embeds are created."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            embeds=[{"title": "Integration Embed"}],
            wait=True
        )

        assert result is not None
        assert len(result.embeds) == 1

    def test_webhook_multiple_embeds(self, webhook_with_token):
        """Test webhook with multiple embeds."""
        setup = webhook_with_token

        embeds = [
            {"title": "Embed 1", "description": "First"},
            {"title": "Embed 2", "description": "Second"},
            {"title": "Embed 3", "description": "Third"},
        ]

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            embeds=embeds,
            wait=True
        )

        assert len(result.embeds) == 3


class TestWebhookMessageCleanup:
    """Tests for webhook message cleanup on deletion."""

    def test_delete_webhook_cleans_messages(self, fresh_server):
        """Test that deleting webhook cleans up message records."""
        setup = fresh_server

        webhook = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name="Cleanup Test"
        )

        setup["webhooks"].execute_webhook(
            webhook_id=webhook.id,
            token=webhook.token,
            content="Message 1",
            wait=True
        )

        setup["webhooks"].execute_webhook(
            webhook_id=webhook.id,
            token=webhook.token,
            content="Message 2",
            wait=True
        )

        count_before = setup["db"].fetch_one(
            "SELECT COUNT(*) as count FROM webhook_messages WHERE webhook_id = ?",
            (webhook.id,)
        )
        assert count_before["count"] == 2

        setup["webhooks"].delete_webhook(
            user_id=setup["owner"].id,
            webhook_id=webhook.id
        )

        count_after = setup["db"].fetch_one(
            "SELECT COUNT(*) as count FROM webhook_messages WHERE webhook_id = ?",
            (webhook.id,)
        )
        assert count_after["count"] == 0


class TestMultipleWebhooks:
    """Tests for multiple webhooks in same channel/server."""

    def test_multiple_webhooks_same_channel(self, fresh_server):
        """Test multiple webhooks in same channel."""
        setup = fresh_server

        webhooks = []
        for i in range(3):
            webhook = setup["webhooks"].create_webhook(
                user_id=setup["owner"].id,
                channel_id=setup["channel"].id,
                name=f"Multi Webhook {i}"
            )
            webhooks.append(webhook)

        for i, webhook in enumerate(webhooks):
            result = setup["webhooks"].execute_webhook(
                webhook_id=webhook.id,
                token=webhook.token,
                content=f"Message from webhook {i}",
                wait=True
            )
            assert result.webhook_id == webhook.id

    def test_webhooks_different_channels(self, fresh_server):
        """Test webhooks in different channels."""
        setup = fresh_server
        from src.core.servers import ChannelType

        channel2 = setup["servers"].create_channel(
            user_id=setup["owner"].id,
            server_id=setup["server"].id,
            name="second-channel",
            channel_type=ChannelType.TEXT
        )

        webhook1 = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=setup["channel"].id,
            name="Channel 1 Webhook"
        )

        webhook2 = setup["webhooks"].create_webhook(
            user_id=setup["owner"].id,
            channel_id=channel2.id,
            name="Channel 2 Webhook"
        )

        result1 = setup["webhooks"].execute_webhook(
            webhook_id=webhook1.id,
            token=webhook1.token,
            content="Channel 1 message",
            wait=True
        )

        result2 = setup["webhooks"].execute_webhook(
            webhook_id=webhook2.id,
            token=webhook2.token,
            content="Channel 2 message",
            wait=True
        )

        assert result1.channel_id == setup["channel"].id
        assert result2.channel_id == channel2.id


class TestWebhookTypes:
    """Tests for webhook types."""

    def test_default_type_incoming(self, webhook_with_token):
        """Test that default webhook type is INCOMING."""
        setup = webhook_with_token
        from src.core.webhooks import WebhookType

        assert setup["webhook"].webhook_type == WebhookType.INCOMING

    def test_webhook_type_stored(self, webhook_with_token):
        """Test that webhook type is stored in database."""
        setup = webhook_with_token

        row = setup["db"].fetch_one(
            "SELECT webhook_type FROM webhook_webhooks WHERE id = ?",
            (setup["webhook"].id,)
        )

        assert row["webhook_type"] == "incoming"
