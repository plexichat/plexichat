"""
Tests for URL preview embed generation.
"""

import pytest
from src.core.embeds import (
    EmbedType,
    InvalidUrlError,
    EmbedValidationError,
)


class TestCreateUrlPreview:
    """Tests for creating URL preview embeds."""

    def test_create_url_preview(self, db_and_modules):
        """Test creating URL preview embed."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"url1_{unique_id}",
            email=f"url1_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://example.com/article"
        )

        assert preview is not None
        assert preview.is_url_preview is True
        assert preview.source_url == "https://example.com/article"

    def test_url_preview_has_metadata(self, db_and_modules):
        """Test URL preview has parsed metadata."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"url2_{unique_id}",
            email=f"url2_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://example.com"
        )

        assert preview.title is not None or preview.description is not None

    def test_youtube_url_preview(self, db_and_modules):
        """Test YouTube URL creates video embed."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"url3_{unique_id}",
            email=f"url3_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://youtube.com/watch?v=abc123"
        )

        assert preview.embed_type == EmbedType.VIDEO
        assert preview.provider is not None
        assert preview.provider.name == "YouTube"

    def test_github_url_preview(self, db_and_modules):
        """Test GitHub URL preview."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"url4_{unique_id}",
            email=f"url4_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://github.com/user/repo"
        )

        assert preview.provider is not None
        assert preview.provider.name == "GitHub"

    def test_twitter_url_preview(self, db_and_modules):
        """Test Twitter/X URL preview."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"url5_{unique_id}",
            email=f"url5_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://twitter.com/user/status/123"
        )

        assert preview.embed_type == EmbedType.ARTICLE

    def test_url_preview_with_message(self, fresh_users_with_dm):
        """Test creating URL preview and attaching to message."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        preview = embeds.create_url_preview(
            user_id=user1.id,
            url="https://example.com",
            message_id=msg.id
        )

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 1
        assert message_embeds[0].is_url_preview is True

    def test_url_preview_invalid_url(self, db_and_modules):
        """Test URL preview with invalid URL fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"url6_{unique_id}",
            email=f"url6_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(InvalidUrlError):
            embeds.create_url_preview(
                user_id=user.id,
                url="not-a-valid-url"
            )

    def test_url_preview_javascript_url(self, db_and_modules):
        """Test URL preview rejects JavaScript URL."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"url7_{unique_id}",
            email=f"url7_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(InvalidUrlError):
            embeds.create_url_preview(
                user_id=user.id,
                url="javascript:alert('xss')"
            )


class TestParseUrlMetadata:
    """Tests for parsing URL metadata."""

    def test_parse_url_metadata(self, db_and_modules):
        """Test parsing URL metadata."""
        db, auth, messaging, servers, embeds = db_and_modules

        metadata = embeds.parse_url_metadata("https://example.com")

        assert "url" in metadata
        assert "site_name" in metadata

    def test_parse_youtube_metadata(self, db_and_modules):
        """Test parsing YouTube URL metadata."""
        db, auth, messaging, servers, embeds = db_and_modules

        metadata = embeds.parse_url_metadata("https://youtube.com/watch?v=abc")

        assert metadata["type"] == "video"
        assert metadata["site_name"] == "YouTube"

    def test_parse_github_metadata(self, db_and_modules):
        """Test parsing GitHub URL metadata."""
        db, auth, messaging, servers, embeds = db_and_modules

        metadata = embeds.parse_url_metadata("https://github.com/user/repo")

        assert metadata["site_name"] == "GitHub"

    def test_parse_generic_url_metadata(self, db_and_modules):
        """Test parsing generic URL metadata."""
        db, auth, messaging, servers, embeds = db_and_modules

        metadata = embeds.parse_url_metadata("https://random-site.com/page")

        assert metadata["type"] == "link"
        assert "random-site.com" in metadata["site_name"]


class TestUrlPreviewTypes:
    """Tests for different URL preview embed types."""

    def test_video_embed_type(self, db_and_modules):
        """Test video URL creates video embed type."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"type1_{unique_id}",
            email=f"type1_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://youtube.com/watch?v=test"
        )

        assert preview.embed_type == EmbedType.VIDEO

    def test_article_embed_type(self, db_and_modules):
        """Test article URL creates article embed type."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"type2_{unique_id}",
            email=f"type2_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://twitter.com/user/status/123"
        )

        assert preview.embed_type == EmbedType.ARTICLE

    def test_link_embed_type(self, db_and_modules):
        """Test generic URL creates link embed type."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"type3_{unique_id}",
            email=f"type3_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://example.com/page"
        )

        assert preview.embed_type == EmbedType.LINK


class TestUrlPreviewProvider:
    """Tests for URL preview provider information."""

    def test_provider_name(self, db_and_modules):
        """Test provider name is set."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"prov1_{unique_id}",
            email=f"prov1_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://youtube.com/watch?v=test"
        )

        assert preview.provider is not None
        assert preview.provider.name is not None

    def test_provider_url(self, db_and_modules):
        """Test provider URL is set."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"prov2_{unique_id}",
            email=f"prov2_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://example.com/page"
        )

        assert preview.provider is not None


class TestUrlPreviewImage:
    """Tests for URL preview images."""

    def test_youtube_preview_has_image(self, db_and_modules):
        """Test YouTube preview has thumbnail image."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"img1_{unique_id}",
            email=f"img1_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://youtube.com/watch?v=test"
        )

        assert preview.image is not None
        assert preview.image.url is not None

    def test_github_preview_has_image(self, db_and_modules):
        """Test GitHub preview has OpenGraph image."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"img2_{unique_id}",
            email=f"img2_{unique_id}@example.com",
            password="TestPass123!"
        )

        preview = embeds.create_url_preview(
            user_id=user.id,
            url="https://github.com/user/repo"
        )

        assert preview.image is not None
