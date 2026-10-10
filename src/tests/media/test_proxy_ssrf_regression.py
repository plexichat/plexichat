"""Regression tests for the [P+] SSRF media-proxy finding.

Covers, fail-closed (a rejection exception is required — never an HTTP
status accept):
  1. metadata-IP / private-IP targets blocked (169.254.169.254, 127.0.0.0/8,
     10/8, 172.16/12, 192.168/16, ::1, 0.0.0.0)
  2. redirect-to-private blocked (all redirects rejected, no following)
  3. oversize bodies rejected (Content-Length pre-check AND streaming cap,
     with partial-artifact cleanup)
  4. SVG/XML bombs handled (content-type block, sniff block incl. padded
     polyglots and entity-expansion payloads — rejected before any parsing)
"""

import pytest
from unittest.mock import Mock, patch

from src.core.media.exceptions import ProxyCacheError, ProxyFetchError
from src.core.media.security.proxy import ExternalProxy


def _png_bytes(size=64):
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * (size - 8)


class _FakeDB:
    def fetch_one(self, *args, **kwargs):
        return None

    def fetch_all(self, *args, **kwargs):
        return []

    def execute(self, *args, **kwargs):
        return None


class _ReadingStorage:
    """Minimal storage that actually consumes the stream (like LocalStorage)."""

    def __init__(self):
        self.files = {}
        self.deleted = []

    def store_stream(self, stream, path, content_type, size):
        # Mimic LocalStorage: persist each chunk as it is read, so a
        # mid-stream failure leaves a partial object behind unless the
        # caller cleans it up.
        self.files[path] = b""
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            self.files[path] += chunk
        return path

    def delete(self, path):
        self.deleted.append(path)
        self.files.pop(path, None)
        return True

    def exists(self, path):
        return path in self.files

    def retrieve(self, path):
        return self.files[path]


def _proxy(max_size=None, storage=None):
    kwargs = {"storage_backend": storage or Mock(), "db": Mock()}
    if max_size is not None:
        kwargs["max_size"] = max_size
    return ExternalProxy(**kwargs)


def _mock_response(
    payload=None, content_type="image/png", status=200, extra_headers=None
):
    resp = Mock()
    resp.status = status
    resp.headers = {"Content-Type": content_type}
    if extra_headers:
        resp.headers.update(extra_headers)
    body = payload if payload is not None else _png_bytes()
    resp.stream.return_value = iter([body])
    return resp


def _pool_for(resp, patch_target):
    pool = Mock()
    pool.urlopen.return_value = resp
    return patch(patch_target, return_value=pool)


class TestMetadataAndPrivateIpBlocked:
    """fetch() must reject SSRF targets without any outbound connection."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://169.254.169.254:80/",
            "http://127.0.0.1/admin",
            "http://127.0.0.2:8080/",
            "http://10.0.0.1/",
            "http://172.16.5.4:3000/",
            "http://192.168.1.1/",
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://localhost/",
        ],
    )
    def test_private_targets_rejected(self, url):
        proxy = _proxy()
        with pytest.raises(ProxyFetchError):
            proxy.fetch(url)

    def test_metadata_ip_rejected_without_dns_or_connect(self):
        proxy = _proxy()
        with (
            patch("src.core.media.security.proxy.HTTPConnectionPool") as http_pool,
            patch("src.core.media.security.proxy.HTTPSConnectionPool") as https_pool,
        ):
            with pytest.raises(ProxyFetchError):
                proxy.fetch("http://169.254.169.254/latest/meta-data/role")
        http_pool.assert_not_called()
        https_pool.assert_not_called()


class TestRedirectToPrivateBlocked:
    """No redirect is ever followed, including ones pointing at private IPs."""

    def test_redirect_not_followed(self):
        proxy = _proxy()
        resp = _mock_response(
            status=302,
            extra_headers={"Location": "http://169.254.169.254/latest/meta-data/"},
        )
        with _pool_for(resp, "src.core.media.security.proxy.HTTPSConnectionPool"):
            with pytest.raises(ProxyFetchError, match="Redirects are not allowed"):
                proxy._fetch_url(
                    "https://example.com/a.png", "example.com", "93.184.216.34"
                )

    def test_redirect_flag_disabled_on_wire(self):
        proxy = _proxy()
        pool = Mock()
        pool.urlopen.return_value = _mock_response()
        with patch(
            "src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool
        ):
            proxy._fetch_url(
                "https://example.com/a.png", "example.com", "93.184.216.34"
            )
        assert pool.urlopen.call_args[1]["redirect"] is False


class TestOversizeBodyRejected:
    """Content-Length pre-check plus enforced streaming cap (fail-closed)."""

    def test_content_length_precheck_rejects(self):
        proxy = _proxy()
        resp = _mock_response(
            extra_headers={"Content-Length": str(10 * 1024 * 1024 + 1)}
        )
        with _pool_for(resp, "src.core.media.security.proxy.HTTPSConnectionPool"):
            with pytest.raises(ProxyFetchError, match="Content too large"):
                proxy._fetch_url(
                    "https://example.com/a.png", "example.com", "93.184.216.34"
                )

    def test_streaming_cap_enforced_without_content_length(self):
        storage = _ReadingStorage()
        proxy = ExternalProxy(storage_backend=storage, db=_FakeDB(), max_size=1024)
        big_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096
        resp = _mock_response(payload=big_png)
        with (
            patch.object(
                proxy._url_validator,
                "validate_url_for_request",
                return_value=("example.com", "93.184.216.34"),
            ),
            _pool_for(resp, "src.core.media.security.proxy.HTTPSConnectionPool"),
        ):
            with pytest.raises((ProxyFetchError, ProxyCacheError)):
                proxy.fetch("https://example.com/a.png")

    def test_partial_artifact_cleaned_up(self):
        # Fail DURING store_stream (not during fetch): the first chunk must
        # fit the sniff window + cap, with later chunks pushing over it.
        storage = _ReadingStorage()
        proxy = ExternalProxy(storage_backend=storage, db=_FakeDB(), max_size=5000)
        chunk1 = b"\x89PNG\r\n\x1a\n" + b"\x00" * (4096 - 8)
        chunk2 = b"\x00" * 4096
        resp = _mock_response(payload=None)
        resp.stream.return_value = iter([chunk1, chunk2])
        with (
            patch.object(
                proxy._url_validator,
                "validate_url_for_request",
                return_value=("example.com", "93.184.216.34"),
            ),
            _pool_for(resp, "src.core.media.security.proxy.HTTPSConnectionPool"),
        ):
            with pytest.raises(ProxyCacheError):
                proxy.fetch("https://example.com/a.png")
        # Nothing attacker-controlled may linger under the cache path.
        assert storage.files == {}
        assert len(storage.deleted) >= 1


class TestSvgBombHandled:
    """SVG/XML is rejected before any parsing (no entity-expansion surface)."""

    def test_svg_content_type_blocked(self):
        proxy = _proxy()
        resp = _mock_response(
            payload=b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )
        with _pool_for(resp, "src.core.media.security.proxy.HTTPSConnectionPool"):
            with pytest.raises(ProxyFetchError, match="Content type not allowed"):
                proxy._fetch_url(
                    "https://example.com/a.svg", "example.com", "93.184.216.34"
                )

    def test_billion_laughs_payload_blocked_before_parsing(self):
        bomb = (
            b'<?xml version="1.0"?>'
            b"<!DOCTYPE lolz ["
            b'<!ENTITY lol "lollollollollollollollol">'
            b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            b"]>"
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>&lol2;</text></svg>'
        )
        proxy = _proxy()
        resp = _mock_response(payload=bomb, content_type="image/png")
        with _pool_for(resp, "src.core.media.security.proxy.HTTPSConnectionPool"):
            with pytest.raises(ProxyFetchError, match="SVG content is blocked"):
                proxy._fetch_url(
                    "https://example.com/a.png", "example.com", "93.184.216.34"
                )

    def test_padded_svg_polyglot_blocked(self):
        # Leading-whitespace/BOM padding must not push the marker out of
        # the sniff window (600 + 2000 bytes of padding).
        for pad_len in (600, 2000):
            payload = b" \t\r\n" * (pad_len // 4) + b"<svg></svg>"
            proxy = _proxy()
            resp = _mock_response(payload=payload, content_type="image/png")
            with _pool_for(resp, "src.core.media.security.proxy.HTTPSConnectionPool"):
                with pytest.raises(ProxyFetchError, match="SVG content is blocked"):
                    proxy._fetch_url(
                        "https://example.com/a.png",
                        "example.com",
                        "93.184.216.34",
                    )

    def test_valid_png_still_passes(self):
        proxy = _proxy()
        resp = _mock_response(payload=_png_bytes(128))
        with _pool_for(resp, "src.core.media.security.proxy.HTTPSConnectionPool"):
            content_type, wrapper = proxy._fetch_url(
                "https://example.com/a.png", "example.com", "93.184.216.34"
            )
        assert content_type == "image/png"
        assert wrapper.read() == _png_bytes(128)
