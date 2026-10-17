"""Tests for URL preview embed generation and validation."""

import pytest

from src.core.embeds.link_preview import (
    LinkPreviewService,
    PreviewMetadata,
    MetaTagParser,
    DEFAULT_CONFIG,
)
from src.core.embeds.validator import (
    validate_url,
    sanitize_content,
)
from src.core.embeds.exceptions import (
    InvalidUrlError,
    EmbedSanitizationError,
)
from src.core.embeds.models import EmbedType, EmbedProvider


def _make_preview_service(db):
    """Create a LinkPreviewService with a real db for metadata parsing tests."""
    return LinkPreviewService(db)


@pytest.mark.embeds
class TestCreateUrlPreview:
    """Test creating URL preview embeds."""

    def test_create_url_preview_service(self, embeds_manager):
        """Test that LinkPreviewService can be instantiated."""
        service = _make_preview_service(embeds_manager._db)
        assert service is not None
        assert service._config["enabled"] is True

    def test_url_preview_has_metadata(self):
        """Test PreviewMetadata dataclass has required fields."""
        meta = PreviewMetadata(
            url="https://example.com",
            title="Example",
            description="Test description",
            image_url="https://example.com/img.png",
            site_name="Example",
            embed_type="link",
        )
        assert meta.url == "https://example.com"
        assert meta.title == "Example"
        assert meta.description == "Test description"
        assert meta.embed_type == "link"
        d = meta.to_dict()
        assert d["url"] == "https://example.com"
        assert d["type"] == "link"

    def test_preview_metadata_defaults(self):
        """Test PreviewMetadata with only required fields."""
        meta = PreviewMetadata(url="https://example.com")
        assert meta.title is None
        assert meta.description is None
        assert meta.image_url is None
        assert meta.site_name is None
        assert meta.embed_type == "link"
        assert meta.author is None

    def test_generate_preview_disabled(self, db):
        """Test that disabled config raises RuntimeError."""
        import utils.config as config

        old = config.get("embeds", None)
        try:
            config.set("embeds", {"url_preview": {"enabled": False}})
            service = _make_preview_service(db)
            with pytest.raises(RuntimeError, match="disabled"):
                service.generate_preview(1, "https://example.com")
        finally:
            # Restore original config properly
            if old is not None:
                config.set("embeds", old)
            else:
                config.set("embeds", {})

    def test_url_preview_with_message(self, fresh_users_with_dm):
        """Test URL preview attached to message."""
        user1, user2, dm, msg, embeds_mgr, messaging_mgr = fresh_users_with_dm
        # Create a rich embed (URL preview is network-dependent so test via embeds manager)
        embed = embeds_mgr.create_embed(
            user_id=user1.id,
            message_id=msg.id,
            title="Preview Title",
            url="https://example.com",
            embed_type=EmbedType.LINK,
        )
        assert embed is not None
        assert embed.url == "https://example.com"

    def test_url_preview_invalid_url(self):
        """Test invalid URL is rejected by validator."""
        with pytest.raises(InvalidUrlError):
            validate_url("not_a_url", "url")

    def test_url_preview_javascript_url(self):
        """Test JavaScript URL is rejected."""
        with pytest.raises(InvalidUrlError, match="JavaScript"):
            validate_url("javascript:alert(1)", "url")


@pytest.mark.embeds
class TestParseUrlMetadata:
    """Test parsing URL metadata from HTML."""

    def test_parse_og_metadata(self, db):
        """Test parsing OpenGraph metadata from HTML."""
        html = b"""
        <html><head>
        <meta property="og:title" content="Test Title">
        <meta property="og:description" content="Test Description">
        <meta property="og:image" content="https://example.com/img.png">
        <meta property="og:site_name" content="Example">
        </head></html>
        """
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://example.com")
        assert meta.title == "Test Title"
        assert meta.description == "Test Description"
        assert meta.image_url == "https://example.com/img.png"
        assert meta.site_name == "Example"

    def test_parse_youtube_metadata(self, db):
        """Test parsing YouTube-style metadata."""
        html = b"""
        <html><head>
        <meta property="og:title" content="Video Title">
        <meta property="og:type" content="video.other">
        <meta property="og:image" content="https://img.youtube.com/vi/abc/thumb.jpg">
        <meta property="og:site_name" content="YouTube">
        </head></html>
        """
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://youtube.com/watch?v=abc")
        assert meta.title == "Video Title"
        assert meta.embed_type == "video"
        assert meta.site_name == "YouTube"

    def test_parse_github_metadata(self, db):
        """Test parsing GitHub-style metadata."""
        html = b"""
        <html><head>
        <meta property="og:title" content="user/repo: A great project">
        <meta property="og:description" content="A cool GitHub repository">
        <meta property="og:image" content="https://github.com/user/repo.png">
        <meta property="og:site_name" content="GitHub">
        <meta property="og:type" content="object">
        </head></html>
        """
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://github.com/user/repo")
        assert "repo" in meta.title
        assert meta.site_name == "GitHub"

    def test_parse_generic_url_metadata(self, db):
        """Test parsing generic URL metadata with title tag fallback."""
        html = b"""
        <html><head><title>Fallback Title</title>
        <meta name="description" content="Meta description">
        </head></html>
        """
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://example.com")
        assert meta.title == "Fallback Title"
        assert meta.description == "Meta description"
        assert meta.embed_type == "link"

    def test_meta_tag_parser_empty_html(self):
        """Test MetaTagParser with empty HTML."""
        parser = MetaTagParser()
        parser.feed("<html></html>")
        assert parser.meta == {}
        assert parser.title is None


@pytest.mark.embeds
class TestUrlPreviewTypes:
    """Test URL preview embed types."""

    def test_video_embed_type(self, db):
        """Test video embed type from og:type."""
        html = b'<meta property="og:type" content="video.movie">'
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://example.com/video")
        assert meta.embed_type == "video"

    def test_article_embed_type(self, db):
        """Test article embed type from og:type."""
        html = b'<meta property="og:type" content="article">'
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://example.com/article")
        assert meta.embed_type == "article"

    def test_link_embed_type(self, db):
        """Test link embed type (default fallback)."""
        html = b'<meta property="og:type" content="website">'
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://example.com")
        assert meta.embed_type == "link"

    def test_embed_type_enum_values(self):
        """Test EmbedType enum values used in URL previews."""
        assert EmbedType.VIDEO.value == "video"
        assert EmbedType.ARTICLE.value == "article"
        assert EmbedType.LINK.value == "link"


@pytest.mark.embeds
class TestUrlPreviewProvider:
    """Test URL preview provider info."""

    def test_provider_name(self, db):
        """Test provider name from og:site_name."""
        html = b'<meta property="og:site_name" content="YouTube">'
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://youtube.com")
        assert meta.site_name == "YouTube"

    def test_provider_url(self, db):
        """Test provider URL from base URL fallback."""
        html = b"<html></html>"
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://example.com/page")
        assert "example.com" in meta.site_name

    def test_embed_provider_model(self):
        """Test EmbedProvider model."""
        provider = EmbedProvider(name="GitHub", url="https://github.com")
        assert provider.name == "GitHub"
        assert provider.url == "https://github.com"


@pytest.mark.embeds
class TestUrlPreviewImage:
    """Test URL preview images."""

    def test_og_image_url(self, db):
        """Test image URL from og:image."""
        html = b'<meta property="og:image" content="https://img.youtube.com/vi/abc/hq.jpg">'
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://youtube.com")
        assert meta.image_url is not None
        assert "youtube.com" in meta.image_url

    def test_relative_image_resolved(self, db):
        """Test that relative image URLs are resolved."""
        html = b'<meta property="og:image" content="/images/thumb.png">'
        service = _make_preview_service(db)
        meta = service._parse_metadata(html, "https://example.com/page")
        assert meta.image_url is not None
        assert meta.image_url.startswith("https://example.com/")

    def test_default_config_image_proxy(self):
        """Test that image proxying is enabled by default."""
        assert DEFAULT_CONFIG["proxy_images"] is True

    def test_default_config_max_image_size(self):
        """Test that max image size has a reasonable default."""
        assert DEFAULT_CONFIG["max_image_size"] > 0


@pytest.mark.embeds
class TestUrlPreviewValidation:
    """Test URL preview validation and security."""

    def test_validate_valid_url(self):
        """Test that valid URLs pass validation."""
        url = validate_url("https://example.com", "url")
        assert url == "https://example.com"

    def test_validate_data_url_rejected(self):
        """Test that data URLs are rejected."""
        with pytest.raises(InvalidUrlError, match="Data"):
            validate_url("data:text/html,<h1>evil</h1>", "url")

    def test_validate_http_url_accepted(self):
        """Test that http URLs are accepted."""
        url = validate_url("http://example.com", "url")
        assert url == "http://example.com"

    def test_sanitize_xss_rejected(self):
        """Test that XSS content is rejected."""
        with pytest.raises(EmbedSanitizationError):
            sanitize_content("<script>alert(1)</script>", "title")

    def test_sanitize_iframe_rejected(self):
        """Test that iframe content is rejected."""
        with pytest.raises(EmbedSanitizationError):
            sanitize_content('<iframe src="evil.com"></iframe>', "description")

    def test_sanitize_safe_content_passes(self):
        """Test that safe content passes sanitization."""
        result = sanitize_content("Hello world!", "title")
        assert result == "Hello world!"

    def test_default_config_rate_limit(self):
        """Test that rate limiting has sensible defaults."""
        assert DEFAULT_CONFIG["rate_limit_requests"] > 0
        assert DEFAULT_CONFIG["rate_limit_window_seconds"] > 0

    def test_default_config_cache_ttl(self):
        """Test that cache TTL has a sensible default."""
        assert DEFAULT_CONFIG["cache_ttl_seconds"] > 0
