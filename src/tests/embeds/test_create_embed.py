"""
Tests for creating embeds with various fields.
"""

from src.core.embeds import (
    EmbedType,
)


class TestCreateBasicEmbed:
    """Tests for creating basic embeds."""

    def test_create_embed_with_title(self, db_and_modules):
        """Test creating embed with just title."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb1_{unique_id}",
            email=f"emb1_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(user_id=user.id, title="Test Title")

        assert embed is not None
        assert embed.title == "Test Title"
        assert embed.id > 0
        assert embed.created_by == user.id
        assert embed.created_at > 0

    def test_create_embed_with_description(self, db_and_modules):
        """Test creating embed with description."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb2_{unique_id}",
            email=f"emb2_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            description="This is a test description"
        )

        assert embed.description == "This is a test description"

    def test_create_embed_with_title_and_description(self, db_and_modules):
        """Test creating embed with title and description."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb3_{unique_id}",
            email=f"emb3_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="My Title",
            description="My Description"
        )

        assert embed.title == "My Title"
        assert embed.description == "My Description"

    def test_create_embed_with_url(self, db_and_modules):
        """Test creating embed with URL."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb4_{unique_id}",
            email=f"emb4_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Link Title",
            url="https://example.com"
        )

        assert embed.url == "https://example.com"

    def test_create_embed_with_timestamp(self, db_and_modules):
        """Test creating embed with timestamp."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb5_{unique_id}",
            email=f"emb5_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Timed Embed",
            timestamp="2025-01-15T12:00:00Z"
        )

        assert embed.timestamp == "2025-01-15T12:00:00Z"

    def test_create_embed_with_color(self, db_and_modules):
        """Test creating embed with color."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb6_{unique_id}",
            email=f"emb6_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Colored Embed",
            color="#FF5733"
        )

        assert embed.color == "#FF5733"

    def test_create_embed_color_without_hash(self, db_and_modules):
        """Test creating embed with color without hash prefix."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb7_{unique_id}",
            email=f"emb7_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Colored Embed",
            color="00FF00"
        )

        assert embed.color == "#00FF00"


class TestCreateEmbedWithSections:
    """Tests for creating embeds with footer, author, image, thumbnail."""

    def test_create_embed_with_footer(self, db_and_modules):
        """Test creating embed with footer."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb8_{unique_id}",
            email=f"emb8_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Footer Embed",
            footer={"text": "Footer text here"}
        )

        assert embed.footer is not None
        assert embed.footer.text == "Footer text here"

    def test_create_embed_with_footer_and_icon(self, db_and_modules):
        """Test creating embed with footer and icon."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb9_{unique_id}",
            email=f"emb9_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Footer Icon Embed",
            footer={
                "text": "Footer with icon",
                "icon_url": "https://example.com/icon.png"
            }
        )

        assert embed.footer.text == "Footer with icon"
        assert embed.footer.icon_url == "https://example.com/icon.png"

    def test_create_embed_with_author(self, db_and_modules):
        """Test creating embed with author."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb10_{unique_id}",
            email=f"emb10_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Author Embed",
            author={"name": "John Doe"}
        )

        assert embed.author is not None
        assert embed.author.name == "John Doe"

    def test_create_embed_with_full_author(self, db_and_modules):
        """Test creating embed with full author details."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb11_{unique_id}",
            email=f"emb11_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Full Author Embed",
            author={
                "name": "Jane Doe",
                "url": "https://jane.example.com",
                "icon_url": "https://jane.example.com/avatar.png"
            }
        )

        assert embed.author.name == "Jane Doe"
        assert embed.author.url == "https://jane.example.com"
        assert embed.author.icon_url == "https://jane.example.com/avatar.png"

    def test_create_embed_with_image(self, db_and_modules):
        """Test creating embed with image."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb12_{unique_id}",
            email=f"emb12_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Image Embed",
            image={"url": "https://example.com/image.png"}
        )

        assert embed.image is not None
        assert embed.image.url == "https://example.com/image.png"

    def test_create_embed_with_image_dimensions(self, db_and_modules):
        """Test creating embed with image and dimensions."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb13_{unique_id}",
            email=f"emb13_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Sized Image Embed",
            image={
                "url": "https://example.com/image.png",
                "width": 800,
                "height": 600
            }
        )

        assert embed.image.url == "https://example.com/image.png"
        assert embed.image.width == 800
        assert embed.image.height == 600

    def test_create_embed_with_thumbnail(self, db_and_modules):
        """Test creating embed with thumbnail."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb14_{unique_id}",
            email=f"emb14_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Thumbnail Embed",
            thumbnail={"url": "https://example.com/thumb.png"}
        )

        assert embed.thumbnail is not None
        assert embed.thumbnail.url == "https://example.com/thumb.png"


class TestCreateFullEmbed:
    """Tests for creating embeds with all fields."""

    def test_create_full_embed(self, db_and_modules):
        """Test creating embed with all fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb15_{unique_id}",
            email=f"emb15_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Full Embed",
            description="Complete embed with all fields",
            url="https://example.com",
            timestamp="2025-01-15T12:00:00Z",
            color="#FF0000",
            footer={"text": "Footer", "icon_url": "https://example.com/footer.png"},
            image={"url": "https://example.com/image.png", "width": 800, "height": 600},
            thumbnail={"url": "https://example.com/thumb.png", "width": 100, "height": 100},
            author={"name": "Author", "url": "https://author.com", "icon_url": "https://author.com/icon.png"},
            fields=[
                {"name": "Field 1", "value": "Value 1", "inline": True},
                {"name": "Field 2", "value": "Value 2", "inline": False}
            ]
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

    def test_create_embed_with_provider(self, db_and_modules):
        """Test creating embed with provider."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb16_{unique_id}",
            email=f"emb16_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Provider Embed",
            provider={"name": "YouTube", "url": "https://youtube.com"}
        )

        assert embed.provider is not None
        assert embed.provider.name == "YouTube"
        assert embed.provider.url == "https://youtube.com"

    def test_create_embed_default_type(self, db_and_modules):
        """Test embed default type is rich."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb17_{unique_id}",
            email=f"emb17_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(user_id=user.id, title="Default Type")

        assert embed.embed_type == EmbedType.RICH

    def test_create_embed_custom_type(self, db_and_modules):
        """Test creating embed with custom type."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb18_{unique_id}",
            email=f"emb18_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Image Type",
            embed_type=EmbedType.IMAGE
        )

        assert embed.embed_type == EmbedType.IMAGE


class TestGetEmbed:
    """Tests for retrieving embeds."""

    def test_get_embed_by_id(self, db_and_modules):
        """Test getting embed by ID."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"emb19_{unique_id}",
            email=f"emb19_{unique_id}@example.com",
            password="TestPass123!"
        )

        created = embeds.create_embed(user_id=user.id, title="Get Test")
        retrieved = embeds.get_embed(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.title == "Get Test"

    def test_get_nonexistent_embed(self, db_and_modules):
        """Test getting nonexistent embed returns None."""
        db, auth, messaging, servers, embeds = db_and_modules

        result = embeds.get_embed(999999999)

        assert result is None
