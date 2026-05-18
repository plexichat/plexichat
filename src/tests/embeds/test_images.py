"""
Tests for embed image and thumbnail handling.
"""

import pytest
from src.core.embeds import EmbedValidationError
from unittest.mock import patch


class TestEmbedImage:
    """Tests for embed image section."""

    def test_create_embed_with_image_url(self, db, auth_manager):
        """Test creating embed with image URL."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="img1_test",
                email="img1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Image Test",
            image={"url": "https://example.com/image.png"},
        )

        assert embed.image is not None
        assert embed.image.url == "https://example.com/image.png"

    def test_create_embed_with_image_dimensions(self, db, auth_manager):
        """Test creating embed with image dimensions."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="img2_test",
                email="img2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
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

    def test_image_url_validation(self, db, auth_manager):
        """Test image URL must be valid."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="img3_test",
                email="img3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Invalid Image URL",
                image={"url": "not-a-valid-url"},
            )

    def test_image_javascript_url_rejected(self, db, auth_manager):
        """Test JavaScript URL in image is rejected."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="img4_test",
                email="img4_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="JS Image",
                image={"url": "javascript:alert('xss')"},
            )

    def test_image_with_https_url(self, db, auth_manager):
        """Test image with HTTPS URL."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="img5_test",
                email="img5_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="HTTPS Image",
            image={"url": "https://cdn.example.com/images/photo.jpg"},
        )

        assert embed.image.url.startswith("https://")

    def test_image_with_http_url(self, db, auth_manager):
        """Test image with HTTP URL."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="img6_test",
                email="img6_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="HTTP Image",
            image={"url": "http://example.com/image.png"},
        )

        assert embed.image.url.startswith("http://")


class TestEmbedThumbnail:
    """Tests for embed thumbnail section."""

    def test_create_embed_with_thumbnail_url(self, db, auth_manager):
        """Test creating embed with thumbnail URL."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="thm1_test",
                email="thm1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Thumbnail Test",
            thumbnail={"url": "https://example.com/thumb.png"},
        )

        assert embed.thumbnail is not None
        assert embed.thumbnail.url == "https://example.com/thumb.png"

    def test_create_embed_with_thumbnail_dimensions(self, db, auth_manager):
        """Test creating embed with thumbnail dimensions."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="thm2_test",
                email="thm2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
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

    def test_thumbnail_url_validation(self, db, auth_manager):
        """Test thumbnail URL must be valid."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="thm3_test",
                email="thm3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Invalid Thumbnail URL",
                thumbnail={"url": "invalid-url"},
            )

    def test_thumbnail_data_url_rejected(self, db, auth_manager):
        """Test data URL in thumbnail is rejected."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="thm4_test",
                email="thm4_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Data Thumbnail",
                thumbnail={"url": "data:image/png;base64,abc123"},
            )


class TestImageAndThumbnailTogether:
    """Tests for using both image and thumbnail."""

    def test_embed_with_both_image_and_thumbnail(self, db, auth_manager):
        """Test embed with both image and thumbnail."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="both1_test",
                email="both1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
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

    def test_embed_image_without_thumbnail(self, db, auth_manager):
        """Test embed with image but no thumbnail."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="both2_test",
                email="both2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Image Only",
            image={"url": "https://example.com/image.png"},
        )

        assert embed.image is not None
        assert embed.thumbnail is None

    def test_embed_thumbnail_without_image(self, db, auth_manager):
        """Test embed with thumbnail but no image."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="both3_test",
                email="both3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Thumbnail Only",
            thumbnail={"url": "https://example.com/thumb.png"},
        )

        assert embed.image is None
        assert embed.thumbnail is not None


class TestImageFormats:
    """Tests for various image URL formats."""

    def test_image_with_query_params(self, db, auth_manager):
        """Test image URL with query parameters."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fmt1_test",
                email="fmt1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Query Params",
            image={"url": "https://example.com/image.png?size=large&format=webp"},
        )

        assert "?" in embed.image.url

    def test_image_with_path(self, db, auth_manager):
        """Test image URL with path."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fmt2_test",
                email="fmt2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Path URL",
            image={"url": "https://cdn.example.com/uploads/2025/01/image.png"},
        )

        assert "/uploads/" in embed.image.url

    def test_image_with_port(self, db, auth_manager):
        """Test image URL with port number."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fmt3_test",
                email="fmt3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Port URL",
            image={"url": "https://example.com:8080/image.png"},
        )

        assert ":8080" in embed.image.url
