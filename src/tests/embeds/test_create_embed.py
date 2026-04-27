"""
Tests for creating embeds with various fields.
"""

from src.core.embeds import EmbedType
from unittest.mock import patch


class TestCreateBasicEmbed:
    """Tests for creating basic embeds."""

    def test_create_embed_with_title(self, db, auth_manager):
        """Test creating embed with just title."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb1_test",
                email="emb1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(user_id=user.id, title="Test Title")

        assert embed is not None
        assert embed.title == "Test Title"
        assert embed.id > 0
        assert embed.created_by == user.id
        assert embed.created_at > 0

    def test_create_embed_with_description(self, db, auth_manager):
        """Test creating embed with description."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb2_test",
                email="emb2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, description="This is a test description"
        )

        assert embed.description == "This is a test description"

    def test_create_embed_with_title_and_description(self, db, auth_manager):
        """Test creating embed with title and description."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb3_test",
                email="emb3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="My Title", description="My Description"
        )

        assert embed.title == "My Title"
        assert embed.description == "My Description"

    def test_create_embed_with_url(self, db, auth_manager):
        """Test creating embed with URL."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb4_test",
                email="emb4_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Link Title", url="https://example.com"
        )

        assert embed.url == "https://example.com"

    def test_create_embed_with_timestamp(self, db, auth_manager):
        """Test creating embed with timestamp."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb5_test",
                email="emb5_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Timed Embed", timestamp="2025-01-15T12:00:00Z"
        )

        assert embed.timestamp == "2025-01-15T12:00:00Z"

    def test_create_embed_with_color(self, db, auth_manager):
        """Test creating embed with color."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb6_test",
                email="emb6_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Colored Embed", color="#FF5733"
        )

        assert embed.color == "#FF5733"

    def test_create_embed_color_without_hash(self, db, auth_manager):
        """Test creating embed with color without hash prefix."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb7_test",
                email="emb7_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Colored Embed", color="00FF00"
        )

        assert embed.color == "#00FF00"


class TestCreateEmbedWithSections:
    """Tests for creating embeds with footer, author, image, thumbnail."""

    def test_create_embed_with_footer(self, db, auth_manager):
        """Test creating embed with footer."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb8_test",
                email="emb8_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Footer Embed", footer={"text": "Footer text here"}
        )

        assert embed.footer is not None
        assert embed.footer.text == "Footer text here"

    def test_create_embed_with_footer_and_icon(self, db, auth_manager):
        """Test creating embed with footer and icon."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb9_test",
                email="emb9_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Footer Icon Embed",
            footer={
                "text": "Footer with icon",
                "icon_url": "https://example.com/icon.png",
            },
        )

        assert embed.footer.text == "Footer with icon"
        assert embed.footer.icon_url == "https://example.com/icon.png"

    def test_create_embed_with_author(self, db, auth_manager):
        """Test creating embed with author."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb10_test",
                email="emb10_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Author Embed", author={"name": "John Doe"}
        )

        assert embed.author is not None
        assert embed.author.name == "John Doe"

    def test_create_embed_with_full_author(self, db, auth_manager):
        """Test creating embed with full author details."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb11_test",
                email="emb11_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Full Author Embed",
            author={
                "name": "Jane Doe",
                "url": "https://jane.example.com",
                "icon_url": "https://jane.example.com/avatar.png",
            },
        )

        assert embed.author.name == "Jane Doe"
        assert embed.author.url == "https://jane.example.com"
        assert embed.author.icon_url == "https://jane.example.com/avatar.png"

    def test_create_embed_with_image(self, db, auth_manager):
        """Test creating embed with image."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb12_test",
                email="emb12_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Image Embed",
            image={"url": "https://example.com/image.png"},
        )

        assert embed.image is not None
        assert embed.image.url == "https://example.com/image.png"

    def test_create_embed_with_image_dimensions(self, db, auth_manager):
        """Test creating embed with image and dimensions."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb13_test",
                email="emb13_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Sized Image Embed",
            image={"url": "https://example.com/image.png", "width": 800, "height": 600},
        )

        assert embed.image.url == "https://example.com/image.png"
        assert embed.image.width == 800
        assert embed.image.height == 600

    def test_create_embed_with_thumbnail(self, db, auth_manager):
        """Test creating embed with thumbnail."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb14_test",
                email="emb14_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Thumbnail Embed",
            thumbnail={"url": "https://example.com/thumb.png"},
        )

        assert embed.thumbnail is not None
        assert embed.thumbnail.url == "https://example.com/thumb.png"


class TestCreateFullEmbed:
    """Tests for creating embeds with all fields."""

    def test_create_full_embed(self, db, auth_manager):
        """Test creating embed with all fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb15_test",
                email="emb15_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Full Embed",
            description="Complete embed with all fields",
            url="https://example.com",
            timestamp="2025-01-15T12:00:00Z",
            color="#FF0000",
            footer={"text": "Footer", "icon_url": "https://example.com/footer.png"},
            image={"url": "https://example.com/image.png", "width": 800, "height": 600},
            thumbnail={
                "url": "https://example.com/thumb.png",
                "width": 100,
                "height": 100,
            },
            author={
                "name": "Author",
                "url": "https://author.com",
                "icon_url": "https://author.com/icon.png",
            },
            fields=[
                {"name": "Field 1", "value": "Value 1", "inline": True},
                {"name": "Field 2", "value": "Value 2", "inline": False},
            ],
        )

        assert embed.title == "Full Embed"
        assert embed.description == "Complete embed with all fields"
        assert embed.url == "https://example.com"
        assert embed.timestamp == "2025-01-15T12:00:00Z"
        assert embed.color == "#FF0000"
        assert embed.footer.text == "Footer"
        assert embed.image.url == "https://example.com/image.png"
        assert embed.thumbnail.url == "https://example.com/thumb.png"
        assert embed.author.name == "Author"
        assert len(embed.fields) == 2

    def test_create_embed_with_provider(self, db, auth_manager):
        """Test creating embed with provider."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb16_test",
                email="emb16_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Provider Embed",
            provider={"name": "YouTube", "url": "https://youtube.com"},
        )

        assert embed.provider is not None
        assert embed.provider.name == "YouTube"
        assert embed.provider.url == "https://youtube.com"

    def test_create_embed_default_type(self, db, auth_manager):
        """Test embed default type is rich."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb17_test",
                email="emb17_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(user_id=user.id, title="Default Type")

        assert embed.embed_type == EmbedType.RICH

    def test_create_embed_custom_type(self, db, auth_manager):
        """Test creating embed with custom type."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb18_test",
                email="emb18_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Image Type", embed_type=EmbedType.IMAGE
        )

        assert embed.embed_type == EmbedType.IMAGE


class TestGetEmbed:
    """Tests for retrieving embeds."""

    def test_get_embed_by_id(self, db, auth_manager):
        """Test getting embed by ID."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="emb19_test",
                email="emb19_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        created = embeds._manager.create_embed(user_id=user.id, title="Get Test")
        retrieved = embeds._manager.get_embed(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.title == "Get Test"

    def test_get_nonexistent_embed(self, db):
        """Test getting nonexistent embed returns None."""
        from src.core import embeds

        embeds.setup(db, None, None)
        result = embeds._manager.get_embed(999999999)

        assert result is None
