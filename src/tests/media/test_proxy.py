"""Tests for media external URL proxy security and behavior."""

import hashlib
import pytest
from unittest.mock import Mock, patch

from src.core.media.exceptions import MediaError, ProxyFetchError
from src.core.media.security.proxy import ExternalProxy


def _png_bytes(size=64):
    """A valid PNG payload (signature + padding)."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * (size - 8)


class TestProxyFetchUrl:
    """Unit tests for the SSRF-safe fetch path."""

    def _proxy(self, db=None):
        storage = Mock()
        return ExternalProxy(
            storage_backend=storage, db=db if db is not None else Mock()
        )

    def _mock_response(self, payload=None, content_type="image/png", status=200):
        resp = Mock()
        resp.status = status
        resp.headers = {"Content-Type": content_type}
        resp.stream.return_value = iter(
            [payload if payload is not None else _png_bytes()]
        )
        return resp

    def test_https_connects_to_ip_but_snis_hostname(self):
        proxy = self._proxy()
        resp = self._mock_response()
        pool = Mock()
        pool.urlopen.return_value = resp

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ) as pool_cls:
            ct, wrapper = proxy._fetch_url(
                "https://example.com:8443/img.png", "example.com", "93.184.216.34"
            )

        assert ct == "image/png"
        args, kwargs = pool_cls.call_args
        # Connect host is the validated IP (DNS-rebinding protection)...
        assert args[0] == "93.184.216.34"
        assert args[1] == 8443
        # ...but TLS verification/SNI target the real hostname.
        assert kwargs["server_hostname"] == "example.com"
        assert kwargs["assert_hostname"] == "example.com"
        assert kwargs["cert_reqs"] == "CERT_REQUIRED"

        urlopen_kwargs = pool.urlopen.call_args[1]
        assert urlopen_kwargs["headers"]["Host"] == "example.com:8443"
        assert urlopen_kwargs["preload_content"] is False
        assert urlopen_kwargs["redirect"] is False
        assert urlopen_kwargs["assert_same_host"] is False

    def test_https_default_port_omits_port_in_host_header(self):
        proxy = self._proxy()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response()

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            proxy._fetch_url(
                "https://example.com/img.png", "example.com", "93.184.216.34"
            )

        headers = pool.urlopen.call_args[1]["headers"]
        assert headers["Host"] == "example.com"

    def test_http_uses_ip_and_host_header_with_port(self):
        proxy = self._proxy()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response()

        with patch(
            "src.core.media.security.proxy.HTTPConnectionPool", return_value=pool
        ) as pool_cls:
            proxy._fetch_url(
                "http://example.com:8080/a.png", "example.com", "93.184.216.34"
            )

        args, _kwargs = pool_cls.call_args
        assert args[0] == "93.184.216.34"
        assert args[1] == 8080
        assert pool.urlopen.call_args[1]["headers"]["Host"] == "example.com:8080"

    def test_redirect_status_rejected(self):
        proxy = self._proxy()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response(status=302)

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            with pytest.raises(ProxyFetchError, match="Redirects are not allowed"):
                proxy._fetch_url(
                    "https://example.com/a.png", "example.com", "93.184.216.34"
                )

    def test_invalid_port_rejected(self):
        proxy = self._proxy()
        with pytest.raises(ProxyFetchError, match="Invalid port"):
            proxy._fetch_url(
                "https://example.com:99999/a.png", "example.com", "93.184.216.34"
            )

    def test_svg_content_type_rejected(self):
        proxy = self._proxy()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response(
            payload=b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            with pytest.raises(ProxyFetchError, match="Content type not allowed"):
                proxy._fetch_url(
                    "https://example.com/a.svg", "example.com", "93.184.216.34"
                )

    def test_svg_bytes_sniffed_under_raster_content_type(self):
        proxy = self._proxy()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response(
            payload=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            content_type="image/png",
        )

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            with pytest.raises(ProxyFetchError, match="SVG content is blocked"):
                proxy._fetch_url(
                    "https://example.com/a.png", "example.com", "93.184.216.34"
                )

    def test_svg_with_leading_bom_and_whitespace_sniffed(self):
        proxy = self._proxy()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response(
            payload=b"\xef\xbb\xbf\n  <svg></svg>", content_type="image/png"
        )

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            with pytest.raises(ProxyFetchError, match="SVG content is blocked"):
                proxy._fetch_url(
                    "https://example.com/a.png", "example.com", "93.184.216.34"
                )

    def test_magic_byte_mismatch_rejected(self):
        proxy = self._proxy()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response(
            payload=_png_bytes(), content_type="image/jpeg"
        )

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            with pytest.raises(ProxyFetchError, match="does not match declared type"):
                proxy._fetch_url(
                    "https://example.com/a.jpg", "example.com", "93.184.216.34"
                )

    def test_webp_magic_validation(self):
        proxy = self._proxy()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response(
            payload=b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20,
            content_type="image/webp",
        )

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            ct, wrapper = proxy._fetch_url(
                "https://example.com/a.webp", "example.com", "93.184.216.34"
            )
        assert ct == "image/webp"

    def test_webp_magic_mismatch_rejected(self):
        proxy = self._proxy()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response(
            payload=b"RIFF\x00\x00\x00\x00PNG " + b"\x00" * 20,
            content_type="image/webp",
        )

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            with pytest.raises(ProxyFetchError, match="does not match declared type"):
                proxy._fetch_url(
                    "https://example.com/a.webp", "example.com", "93.184.216.34"
                )

    def test_stream_includes_sniffed_bytes_and_hash(self):
        proxy = self._proxy()
        payload = _png_bytes()
        pool = Mock()
        pool.urlopen.return_value = self._mock_response(payload=payload)

        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            _ct, wrapper = proxy._fetch_url(
                "https://example.com/a.png", "example.com", "93.184.216.34"
            )

        data = wrapper.read()
        assert data == payload
        assert wrapper.bytes_read == len(payload)
        assert wrapper.hex_digest == hashlib.sha256(payload).hexdigest()


class TestProxyUnavailable:
    """MediaManager proxy accessors raise when the proxy is disabled."""

    def test_proxy_url_raises_when_unavailable(self, media_manager):
        media_manager._proxy = None
        with pytest.raises(MediaError, match="Proxy not available"):
            media_manager.proxy_url("https://example.com/image.png")

    def test_get_proxied_content_raises_when_unavailable(self, media_manager):
        media_manager._proxy = None
        with pytest.raises(MediaError, match="Proxy not available"):
            media_manager.get_proxied_content("https://example.com/image.png")
