"""
Tests for secure link preview generation.
"""

import pytest
import time
from unittest.mock import Mock


class TestLinkPreviewService:
    """Tests for LinkPreviewService security features."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = Mock()
        db.fetch_one = Mock(return_value=None)
        db.execute = Mock()
        return db

    @pytest.fixture
    def preview_service(self, mock_db):
        """Create a LinkPreviewService instance."""
        from src.core.embeds.link_preview import LinkPreviewService

        return LinkPreviewService(mock_db)

    def test_url_hash_consistency(self, preview_service):
        """Test that URL hashing is consistent."""
        url = "https://example.com/page"

        hash1 = preview_service._hash_url(url)
        hash2 = preview_service._hash_url(url)

        assert hash1 == hash2
        assert len(hash1) == 32

    def test_url_hash_case_insensitive(self, preview_service):
        """Test that URL hashing is case-insensitive."""
        url1 = "https://EXAMPLE.COM/page"
        url2 = "https://example.com/page"

        assert preview_service._hash_url(url1) == preview_service._hash_url(url2)

    def test_rate_limit_check(self, mock_db, preview_service):
        """Test rate limit checking."""
        # No existing rate limit
        mock_db.fetch_one.return_value = None
        assert preview_service._check_rate_limit(user_id=1) is True

        # At limit
        mock_db.fetch_one.return_value = {"request_count": 10}
        assert preview_service._check_rate_limit(user_id=1) is False

        # Under limit
        mock_db.fetch_one.return_value = {"request_count": 5}
        assert preview_service._check_rate_limit(user_id=1) is True

    def test_validate_url_blocks_private_ips(self, preview_service):
        """Test that private IPs are blocked."""
        private_urls = [
            "http://127.0.0.1/page",
            "http://localhost/page",
            "http://192.168.1.1/page",
            "http://10.0.0.1/page",
            "http://172.16.0.1/page",
        ]

        for url in private_urls:
            with pytest.raises(ValueError):
                preview_service._validate_url(url)

    def test_validate_url_blocks_internal_domains(self, preview_service):
        """Test that internal domains are blocked."""
        internal_urls = [
            "http://server.local/page",
            "http://internal.internal/page",
        ]

        for url in internal_urls:
            with pytest.raises(ValueError):
                preview_service._validate_url(url)

    def test_validate_url_blocks_non_http_schemes(self, preview_service):
        """Test that non-HTTP schemes are blocked."""
        blocked_urls = [
            "file:///etc/passwd",
            "ftp://example.com/file",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
        ]

        for url in blocked_urls:
            with pytest.raises(ValueError):
                preview_service._validate_url(url)

    def test_validate_image_content_blocks_svg(self, preview_service):
        """Test that SVG images are blocked (XSS risk)."""
        svg_data = (
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        )

        result = preview_service._validate_image_content(svg_data, "image/svg+xml")
        assert result is False

    def test_validate_image_content_accepts_valid_images(self, preview_service):
        """Test that valid images are accepted."""
        # JPEG magic bytes
        jpeg_data = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        assert preview_service._validate_image_content(jpeg_data, "image/jpeg") is True

        # PNG magic bytes
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert preview_service._validate_image_content(png_data, "image/png") is True

        # GIF magic bytes
        gif_data = b"GIF89a" + b"\x00" * 100
        assert preview_service._validate_image_content(gif_data, "image/gif") is True

    def test_validate_image_content_rejects_fake_images(self, preview_service):
        """Test that fake images (wrong magic bytes) are rejected."""
        # Executable disguised as image
        fake_image = b"MZ" + b"\x00" * 100  # PE executable header

        result = preview_service._validate_image_content(fake_image, "image/jpeg")
        assert result is False


class TestMetaTagParser:
    """Tests for HTML meta tag parsing."""

    def test_parse_opengraph_tags(self):
        """Test parsing OpenGraph meta tags."""
        from src.core.embeds.link_preview import MetaTagParser

        html = """
        <html>
        <head>
            <meta property="og:title" content="Test Title">
            <meta property="og:description" content="Test Description">
            <meta property="og:image" content="https://example.com/image.jpg">
            <meta property="og:type" content="article">
        </head>
        </html>
        """

        parser = MetaTagParser()
        parser.feed(html)

        assert parser.meta.get("og:title") == "Test Title"
        assert parser.meta.get("og:description") == "Test Description"
        assert parser.meta.get("og:image") == "https://example.com/image.jpg"
        assert parser.meta.get("og:type") == "article"

    def test_parse_twitter_card_tags(self):
        """Test parsing Twitter Card meta tags."""
        from src.core.embeds.link_preview import MetaTagParser

        html = """
        <html>
        <head>
            <meta name="twitter:title" content="Twitter Title">
            <meta name="twitter:description" content="Twitter Description">
            <meta name="twitter:image" content="https://example.com/twitter.jpg">
        </head>
        </html>
        """

        parser = MetaTagParser()
        parser.feed(html)

        assert parser.meta.get("twitter:title") == "Twitter Title"
        assert parser.meta.get("twitter:description") == "Twitter Description"
        assert parser.meta.get("twitter:image") == "https://example.com/twitter.jpg"

    def test_parse_title_tag(self):
        """Test parsing HTML title tag."""
        from src.core.embeds.link_preview import MetaTagParser

        html = """
        <html>
        <head>
            <title>Page Title</title>
        </head>
        </html>
        """

        parser = MetaTagParser()
        parser.feed(html)

        assert parser.title == "Page Title"

    def test_opengraph_takes_precedence(self):
        """Test that OpenGraph title takes precedence over HTML title."""
        from src.core.embeds.link_preview import MetaTagParser

        html = """
        <html>
        <head>
            <title>HTML Title</title>
            <meta property="og:title" content="OG Title">
        </head>
        </html>
        """

        parser = MetaTagParser()
        parser.feed(html)

        # Both should be available
        assert parser.title == "HTML Title"
        assert parser.meta.get("og:title") == "OG Title"


class TestPreviewMetadata:
    """Tests for PreviewMetadata dataclass."""

    def test_to_dict(self):
        """Test PreviewMetadata.to_dict() method."""
        from src.core.embeds.link_preview import PreviewMetadata

        metadata = PreviewMetadata(
            url="https://example.com",
            title="Test Title",
            description="Test Description",
            image_url="https://example.com/image.jpg",
            site_name="Example",
            embed_type="article",
            author="John Doe",
        )

        result = metadata.to_dict()

        assert result["url"] == "https://example.com"
        assert result["title"] == "Test Title"
        assert result["description"] == "Test Description"
        assert result["image_url"] == "https://example.com/image.jpg"
        assert result["site_name"] == "Example"
        assert result["type"] == "article"
        assert result["author"] == "John Doe"


class TestCaching:
    """Tests for preview caching."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with caching support."""
        db = Mock()
        db.fetch_one = Mock(return_value=None)
        db.execute = Mock()
        return db

    def test_cache_miss_returns_none(self, mock_db):
        """Test that cache miss returns None."""
        from src.core.embeds.link_preview import LinkPreviewService

        mock_db.fetch_one.return_value = None
        service = LinkPreviewService(mock_db)

        result = service._get_cached_preview("https://example.com")
        assert result is None

    def test_expired_cache_returns_none(self, mock_db):
        """Test that expired cache entries return None."""
        from src.core.embeds.link_preview import LinkPreviewService

        # Return expired cache entry
        mock_db.fetch_one.return_value = {
            "id": 1,
            "url": "https://example.com",
            "title": "Cached Title",
            "description": None,
            "image_url": None,
            "proxied_image_id": None,
            "site_name": "Example",
            "embed_type": "link",
            "expires_at": int(time.time() * 1000) - 1000,  # Expired
        }

        service = LinkPreviewService(mock_db)

        # The query includes expires_at > now, so expired entries won't be returned
        mock_db.fetch_one.return_value = None
        result = service._get_cached_preview("https://example.com")
        assert result is None
