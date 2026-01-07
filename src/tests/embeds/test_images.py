"""
Tests for embed image and thumbnail handling.
"""

import pytest
from src.core.embeds import EmbedValidationError


class TestEmbedImage:
    """Tests for embed image section."""

    def test_create_embed_with_image_url(self, db_and_modules):
        """Test creating embed with image URL."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"img1_{unique_id}",
            email=f"img1_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Image Test",
            image={"url": "https://example.com/image.png"},
        )

        assert embed.image is not None
        assert embed.image.url == "https://example.com/image.png"

    def test_create_embed_with_image_dimensions(self, db_and_modules):
        """Test creating embed with image dimensions."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"img2_{unique_id}",
            email=f"img2_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Sized Image",
            image={
                "url": "https://example.com/image.png",
                "width": 1920,
                "height": 1080,
            },
        )

        assert embed.image.width == 1920
        assert embed.image.height == 1080

    def test_image_url_validation(self, db_and_modules):
        """Test image URL must be valid."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"img3_{unique_id}",
            email=f"img3_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Invalid Image URL",
                image={"url": "not-a-valid-url"},
            )

    def test_image_javascript_url_rejected(self, db_and_modules):
        """Test JavaScript URL in image is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"img4_{unique_id}",
            email=f"img4_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="JS Image",
                image={"url": "javascript:alert('xss')"},
            )

    def test_image_with_https_url(self, db_and_modules):
        """Test image with HTTPS URL."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"img5_{unique_id}",
            email=f"img5_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="HTTPS Image",
            image={"url": "https://cdn.example.com/images/photo.jpg"},
        )

        assert embed.image.url.startswith("https://")

    def test_image_with_http_url(self, db_and_modules):
        """Test image with HTTP URL."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"img6_{unique_id}",
            email=f"img6_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="HTTP Image",
            image={"url": "http://example.com/image.png"},
        )

        assert embed.image.url.startswith("http://")


class TestEmbedThumbnail:
    """Tests for embed thumbnail section."""

    def test_create_embed_with_thumbnail_url(self, db_and_modules):
        """Test creating embed with thumbnail URL."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"thm1_{unique_id}",
            email=f"thm1_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Thumbnail Test",
            thumbnail={"url": "https://example.com/thumb.png"},
        )

        assert embed.thumbnail is not None
        assert embed.thumbnail.url == "https://example.com/thumb.png"

    def test_create_embed_with_thumbnail_dimensions(self, db_and_modules):
        """Test creating embed with thumbnail dimensions."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"thm2_{unique_id}",
            email=f"thm2_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Sized Thumbnail",
            thumbnail={
                "url": "https://example.com/thumb.png",
                "width": 128,
                "height": 128,
            },
        )

        assert embed.thumbnail.width == 128
        assert embed.thumbnail.height == 128

    def test_thumbnail_url_validation(self, db_and_modules):
        """Test thumbnail URL must be valid."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"thm3_{unique_id}",
            email=f"thm3_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Invalid Thumbnail URL",
                thumbnail={"url": "invalid-url"},
            )

    def test_thumbnail_data_url_rejected(self, db_and_modules):
        """Test data URL in thumbnail is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"thm4_{unique_id}",
            email=f"thm4_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Data Thumbnail",
                thumbnail={"url": "data:image/png;base64,abc123"},
            )


class TestImageAndThumbnailTogether:
    """Tests for using both image and thumbnail."""

    def test_embed_with_both_image_and_thumbnail(self, db_and_modules):
        """Test embed with both image and thumbnail."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"both1_{unique_id}",
            email=f"both1_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Both Images",
            image={"url": "https://example.com/main.png", "width": 800, "height": 600},
            thumbnail={
                "url": "https://example.com/thumb.png",
                "width": 100,
                "height": 100,
            },
        )

        assert embed.image is not None
        assert embed.thumbnail is not None
        assert embed.image.url != embed.thumbnail.url

    def test_embed_image_without_thumbnail(self, db_and_modules):
        """Test embed with image but no thumbnail."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"both2_{unique_id}",
            email=f"both2_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Image Only",
            image={"url": "https://example.com/image.png"},
        )

        assert embed.image is not None
        assert embed.thumbnail is None

    def test_embed_thumbnail_without_image(self, db_and_modules):
        """Test embed with thumbnail but no image."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"both3_{unique_id}",
            email=f"both3_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Thumbnail Only",
            thumbnail={"url": "https://example.com/thumb.png"},
        )

        assert embed.image is None
        assert embed.thumbnail is not None


class TestImageFormats:
    """Tests for various image URL formats."""

    def test_image_with_query_params(self, db_and_modules):
        """Test image URL with query parameters."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fmt1_{unique_id}",
            email=f"fmt1_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Query Params",
            image={"url": "https://example.com/image.png?size=large&format=webp"},
        )

        assert "?" in embed.image.url

    def test_image_with_path(self, db_and_modules):
        """Test image URL with path."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fmt2_{unique_id}",
            email=f"fmt2_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Path URL",
            image={"url": "https://cdn.example.com/uploads/2025/01/image.png"},
        )

        assert "/uploads/" in embed.image.url

    def test_image_with_port(self, db_and_modules):
        """Test image URL with port number."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fmt3_{unique_id}",
            email=f"fmt3_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Port URL",
            image={"url": "https://example.com:8080/image.png"},
        )

        assert ":8080" in embed.image.url
