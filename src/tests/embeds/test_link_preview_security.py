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

    def test_url_hash_preserves_path_case(self, preview_service):
        """Path case is significant per RFC 3986 and must not collide.

        Lowercasing the whole URL made these two distinct resources share one
        cache row, so previewing one returned the other's cached metadata.
        """
        upper = preview_service._hash_url("https://example.com/ABCDEF")
        lower = preview_service._hash_url("https://example.com/abcdef")

        assert upper != lower

    def test_url_hash_preserves_query_case(self, preview_service):
        """Query-string case is significant and must not collide either."""
        upper = preview_service._hash_url("https://example.com/page?Key=AbC")
        lower = preview_service._hash_url("https://example.com/page?key=abc")

        assert upper != lower

    def test_url_hash_still_normalizes_scheme_and_host(self, preview_service):
        """Scheme/host remain case-insensitive, including with a port."""
        assert preview_service._hash_url("HTTPS://EXAMPLE.com/a") == (
            preview_service._hash_url("https://example.com/a")
        )
        assert preview_service._hash_url("https://Example.com:8443/a") == (
            preview_service._hash_url("https://example.com:8443/a")
        )

    def test_url_hash_distinguishes_port(self, preview_service):
        """A non-default port is part of the origin and must be preserved."""
        assert preview_service._hash_url("https://example.com:8443/a") != (
            preview_service._hash_url("https://example.com/a")
        )

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

    def test_proxy_image_disallows_redirects(self, preview_service):
        """Test proxied images are delegated to the media proxy (no local fetch)."""
        proxied = Mock()
        proxied.id = "proxied_1"
        proxied.content_type = "image/jpeg"
        preview_service._media_proxy = Mock()
        preview_service._media_proxy.fetch = Mock(return_value=proxied)

        proxied_id = preview_service._proxy_image("https://example.com/image.jpg")

        # The LinkPreviewService must not fetch the image itself — the media
        # proxy owns redirect/SSRF handling. Delegate exactly once.
        preview_service._media_proxy.fetch.assert_called_once_with(
            "https://example.com/image.jpg", force_refresh=False
        )
        assert proxied_id == "proxied_1"

    def test_proxy_image_rejects_svg_content_types(self, preview_service):
        """Test proxied images reject SVG responses before caching them."""
        proxied = Mock()
        proxied.id = "proxied_svg"
        proxied.content_type = "image/svg+xml"
        preview_service._media_proxy = Mock()
        preview_service._media_proxy.fetch = Mock(return_value=proxied)

        # An SVG is skipped (preview continues without an image) rather than
        # failing the whole preview.
        assert preview_service._proxy_image("https://example.com/image.svg") is None


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


class _FakeResponse:
    """Minimal httpx.Response stand-in for a 200 text/html body."""

    status_code = 200
    headers = {"content-type": "text/html; charset=utf-8"}

    def __iter__(self):
        return iter([b"<html><head></head><body></body></html>"])

    def iter_bytes(self):
        return iter([b"<html><head></head><body></body></html>"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingClient:
    """Captures the URL and headers each request was issued with."""

    def __init__(self):
        self.calls = []

    def stream(self, method, url, headers=None, follow_redirects=None, extensions=None):
        self.calls.append({"url": url, "headers": headers or {}})
        return _FakeResponse()


class TestFetchWithRedirectsIPv6:
    """The resolved IP substituted into the request URL must stay parseable.

    An IPv6 literal has to carry square brackets inside a URL authority.
    Without them the address and the ``:port`` suffix merge into one
    ambiguous token and every preview fetch for an IPv6 host is unroutable.
    """

    @pytest.fixture
    def service(self):
        from src.core.embeds.link_preview import LinkPreviewService

        db = Mock()
        db.fetch_one.return_value = None
        return LinkPreviewService(db)

    def _run(self, service, hostname, resolved_ip, url):
        client = _RecordingClient()
        service._http_client = client
        service._fetch_with_redirects(url, hostname, resolved_ip)
        assert len(client.calls) == 1
        return client.calls[0]

    @pytest.mark.parametrize(
        ("port", "expected_url", "expected_host"),
        [
            # The request URL always carries the explicit port; only the
            # authority's host token is at issue here.
            (8443, "https://[2606:4700::1111]:8443/a", "ipv6.example.com:8443"),
            (443, "https://[2606:4700::1111]:443/a", "ipv6.example.com"),
        ],
    )
    def test_ipv6_resolved_ip_is_bracketed(
        self, service, port, expected_url, expected_host
    ):
        url = f"https://ipv6.example.com:{port}/a"
        call = self._run(service, "ipv6.example.com", "2606:4700::1111", url)
        assert call["url"] == expected_url
        # The Host header keeps the *original* hostname, never the IP.
        assert call["headers"]["Host"] == expected_host

    def test_ipv6_literal_hostname_is_bracketed_in_host_header(self, service):
        """An IPv6-literal source URL must bracket the Host header.

        ``urlparse().hostname`` strips the brackets from the authority, so
        the un-bracketed form would otherwise be emitted into the header.
        """
        call = self._run(
            service,
            "2606:4700::1111",
            "2606:4700::1111",
            "https://[2606:4700::1111]:8443/a",
        )
        assert call["url"] == "https://[2606:4700::1111]:8443/a"
        assert call["headers"]["Host"] == "[2606:4700::1111]:8443"

    def test_ipv6_request_url_roundtrips_through_urlparse(self, service):
        """The bracketed authority must parse back to the resolved IP."""
        from urllib.parse import urlparse

        call = self._run(
            service,
            "ipv6.example.com",
            "2606:4700::1111",
            "https://ipv6.example.com:8443/a",
        )
        reparsed = urlparse(call["url"])
        assert reparsed.hostname == "2606:4700::1111"
        assert reparsed.port == 8443

    def test_ipv4_request_url_is_unbracketed(self, service):
        """Bracketing is IPv6-only; IPv4 hosts must be left alone."""
        call = self._run(
            service,
            "example.com",
            "93.184.216.34",
            "https://example.com:8443/a",
        )
        assert call["url"] == "https://93.184.216.34:8443/a"
        assert call["headers"]["Host"] == "example.com:8443"
