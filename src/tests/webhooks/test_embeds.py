"""
Tests for webhook messages with embeds.
"""

import pytest


class TestWebhookEmbeds:
    """Tests for sending embeds via webhooks."""

    def test_execute_with_single_embed(self, webhook_with_token):
        """Test sending message with single embed."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Message with embed",
            embeds=[{
                "title": "Test Embed",
                "description": "This is a test embed"
            }],
            wait=True
        )

        assert result is not None
        assert len(result.embeds) == 1
        assert result.embeds[0]["title"] == "Test Embed"

    def test_execute_with_multiple_embeds(self, webhook_with_token):
        """Test sending message with multiple embeds."""
        setup = webhook_with_token

        embeds = [
            {"title": f"Embed {i}", "description": f"Description {i}"}
            for i in range(5)
        ]

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Multiple embeds",
            embeds=embeds,
            wait=True
        )

        assert len(result.embeds) == 5

    def test_execute_with_max_embeds(self, webhook_with_token):
        """Test sending message with maximum embeds."""
        setup = webhook_with_token

        embeds = [
            {"title": f"Embed {i}"}
            for i in range(10)
        ]

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Max embeds",
            embeds=embeds,
            wait=True
        )

        assert len(result.embeds) == 10

    def test_execute_exceeds_embed_limit(self, webhook_with_token):
        """Test sending message exceeding embed limit."""
        setup = webhook_with_token
        from src.core.webhooks import EmbedLimitError

        embeds = [
            {"title": f"Embed {i}"}
            for i in range(11)
        ]

        with pytest.raises(EmbedLimitError) as exc_info:
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                content="Too many embeds",
                embeds=embeds
            )

        assert exc_info.value.max_allowed == 10
        assert exc_info.value.current == 11

    def test_execute_embed_only_no_content(self, webhook_with_token):
        """Test sending embed without content."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            embeds=[{"title": "Embed Only"}],
            wait=True
        )

        assert result is not None
        assert result.content is None or result.content == ""
        assert len(result.embeds) == 1

    def test_execute_rich_embed(self, webhook_with_token):
        """Test sending rich embed with all fields."""
        setup = webhook_with_token

        embed = {
            "title": "Rich Embed",
            "description": "Full featured embed",
            "url": "https://example.com",
            "color": "#FF0000",
            "footer": {"text": "Footer text"},
            "author": {"name": "Author Name"},
            "fields": [
                {"name": "Field 1", "value": "Value 1", "inline": True},
                {"name": "Field 2", "value": "Value 2", "inline": False}
            ]
        }

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            embeds=[embed],
            wait=True
        )

        assert result is not None
        assert len(result.embeds) == 1

    def test_execute_embed_with_image(self, webhook_with_token):
        """Test sending embed with image."""
        setup = webhook_with_token

        embed = {
            "title": "Image Embed",
            "image": {"url": "https://example.com/image.png"}
        }

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            embeds=[embed],
            wait=True
        )

        assert result is not None

    def test_execute_embed_with_thumbnail(self, webhook_with_token):
        """Test sending embed with thumbnail."""
        setup = webhook_with_token

        embed = {
            "title": "Thumbnail Embed",
            "thumbnail": {"url": "https://example.com/thumb.png"}
        }

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            embeds=[embed],
            wait=True
        )

        assert result is not None

    def test_execute_empty_embeds_list(self, webhook_with_token):
        """Test sending with empty embeds list requires content."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidContentError

        with pytest.raises(InvalidContentError):
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                embeds=[]
            )

    def test_execute_content_and_embeds(self, webhook_with_token):
        """Test sending both content and embeds."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Text content",
            embeds=[{"title": "Embed Title"}],
            wait=True
        )

        assert result.content == "Text content"
        assert len(result.embeds) == 1


class TestEmbedWithOverrides:
    """Tests for embeds combined with username/avatar overrides."""

    def test_embed_with_username_override(self, webhook_with_token):
        """Test embed with username override."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            embeds=[{"title": "Override Embed"}],
            username="Custom Embed Bot",
            wait=True
        )

        assert result.username == "Custom Embed Bot"
        assert len(result.embeds) == 1

    def test_embed_with_avatar_override(self, webhook_with_token):
        """Test embed with avatar override."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            embeds=[{"title": "Avatar Embed"}],
            avatar_url="https://example.com/embed-avatar.png",
            wait=True
        )

        assert result.avatar_url == "https://example.com/embed-avatar.png"

    def test_embed_with_all_overrides(self, webhook_with_token):
        """Test embed with all overrides."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Full message",
            embeds=[{"title": "Full Embed"}],
            username="Full Bot",
            avatar_url="https://example.com/full.png",
            wait=True
        )

        assert result.content == "Full message"
        assert result.username == "Full Bot"
        assert result.avatar_url == "https://example.com/full.png"
        assert len(result.embeds) == 1
