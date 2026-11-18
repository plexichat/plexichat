"""Regression tests for the in-process media-proxy content cache.

The proxy's persistent (DB) cache still re-ran the SSRF validator's DNS lookup
on every request, which dominated ``GET /api/v1/media/proxy`` latency. These
tests pin the new bounded in-process cache while proving that no security
check was weakened: the network-free SSRF gate runs on every request, the full
DNS/SSRF validation still runs before any fetch, errors and oversized content
are never cached, and size/content-type rejection is unchanged.
"""

from unittest.mock import Mock, patch

import pytest

from src.core.media.exceptions import ProxyCacheError, ProxyFetchError
from src.core.media.security.proxy import ExternalProxy

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120
_CREATE_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS media_proxy_cache (
    id INTEGER PRIMARY KEY,
    source_url TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    checksum TEXT,
    cached_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_accessed INTEGER NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0
)
"""
_URL = "https://example.com/a.png"


class _FakeStream:
    """Minimal ResponseStreamWrapper stand-in for patched network fetches."""

    def __init__(self, data, content_type="image/png", cache_control="", etag=""):
        self._data = data
        self.bytes_read = len(data)
        self.hex_digest = "0" * 64
        self.cache_control = cache_control
        self.etag = etag
        self.content_type = content_type

    def read(self, size=-1):
        data, self._data = self._data, b""
        return data


def _make_proxy(db, tmp_path, **kwargs):
    from src.core.media.storage.local import LocalStorage

    db.execute(_CREATE_CACHE_TABLE)
    storage = LocalStorage(base_path=str(tmp_path / "cache"), base_url="/media")
    defaults = dict(cache_ttl=86400, max_size=10 * 1024 * 1024)
    defaults.update(kwargs)
    return ExternalProxy(storage_backend=storage, db=db, **defaults)


def _mock_fetch(data=_PNG, content_type="image/png", **meta):
    """Return a patchable ``_fetch_url`` replacement producing valid bytes."""
    wrapper = _FakeStream(data, content_type, **meta)
    return Mock(return_value=(content_type, wrapper))


class TestMemoryCacheHits:
    def test_second_request_served_without_second_fetch_or_dns(
        self, db, tmp_path
    ):
        proxy = _make_proxy(db, tmp_path)
        fetch = _mock_fetch()
        validate = Mock(return_value=("example.com", "93.184.216.34"))
        structure = Mock(wraps=proxy._assert_safe_url_structure)

        with patch.object(proxy, "_fetch_url", fetch), patch.object(
            proxy._url_validator, "validate_url_for_request", validate
        ), patch.object(proxy, "_assert_safe_url_structure", structure):
            first = proxy.get_content(_URL)
            second = proxy.get_content(_URL)

        assert first == second == (_PNG, "image/png")
        # One network fetch and one DNS validation, but the cheap SSRF gate
        # ran on BOTH requests (including the cache hit).
        assert fetch.call_count == 1
        assert validate.call_count == 1
        assert structure.call_count == 2
        assert proxy.content_cache_stats()["hits"] == 1

    def test_cache_stats_report_bounded_entry(self, db, tmp_path):
        proxy = _make_proxy(db, tmp_path)
        with patch.object(proxy, "_fetch_url", _mock_fetch()), patch.object(
            proxy._url_validator,
            "validate_url_for_request",
            return_value=("example.com", "93.184.216.34"),
        ):
            proxy.get_content(_URL)
        stats = proxy.content_cache_stats()
        assert stats["size"] == 1
        assert stats["bytes"] == len(_PNG)
        assert proxy._content_cache.maxsize >= 1
        assert proxy._content_cache.max_bytes >= len(_PNG)


class TestSecurityPreserved:
    def test_ssrf_scheme_gate_still_rejects_on_every_request(self, db, tmp_path):
        proxy = _make_proxy(db, tmp_path)
        # A cached legitimate URL must not make a forbidden URL serveable.
        with patch.object(proxy, "_fetch_url", _mock_fetch()), patch.object(
            proxy._url_validator,
            "validate_url_for_request",
            return_value=("example.com", "93.184.216.34"),
        ):
            proxy.get_content(_URL)

        with pytest.raises(ProxyFetchError, match="not allowed"):
            proxy.get_content("file:///etc/passwd")
        with pytest.raises(ProxyFetchError, match="forbidden"):
            proxy.get_content("http://127.0.0.1/a.png")
        with pytest.raises(ProxyFetchError, match="internal host"):
            proxy.get_content("http://evil.internal/a.png")
        assert proxy.content_cache_stats()["size"] == 1

    def test_full_dns_validation_runs_before_any_fetch(self, db, tmp_path):
        proxy = _make_proxy(db, tmp_path)
        fetch = _mock_fetch()
        validate = Mock(side_effect=ValueError("resolves to forbidden IP 10.0.0.5"))
        with patch.object(proxy, "_fetch_url", fetch), patch.object(
            proxy._url_validator, "validate_url_for_request", validate
        ):
            with pytest.raises(ProxyFetchError, match="forbidden IP"):
                proxy.get_content(_URL)
        fetch.assert_not_called()
        assert proxy.content_cache_stats()["size"] == 0

    def test_oversized_content_rejected_and_not_cached(self, db, tmp_path):
        from src.core.media.security.proxy import ResponseStreamWrapper

        proxy = _make_proxy(db, tmp_path, max_size=64)
        big = _PNG + b"\x00" * 256
        # A real stream wrapper enforces the body-size cap while streaming,
        # just like the production fetch path does.
        wrapper = ResponseStreamWrapper(iter([big]), max_size=64)
        fetch = Mock(return_value=("image/png", wrapper))
        with patch.object(proxy, "_fetch_url", fetch), patch.object(
            proxy._url_validator,
            "validate_url_for_request",
            return_value=("example.com", "93.184.216.34"),
        ):
            with pytest.raises(ProxyCacheError):
                proxy.get_content(_URL)
        assert proxy.content_cache_stats()["size"] == 0

    def test_failed_fetch_is_not_cached(self, db, tmp_path):
        proxy = _make_proxy(db, tmp_path)
        failing = Mock(side_effect=ProxyFetchError("boom", _URL))
        with patch.object(proxy, "_fetch_url", failing), patch.object(
            proxy._url_validator,
            "validate_url_for_request",
            return_value=("example.com", "93.184.216.34"),
        ):
            with pytest.raises(ProxyFetchError):
                proxy.get_content(_URL)
            with pytest.raises(ProxyFetchError):
                proxy.get_content(_URL)
        assert failing.call_count == 2
        assert proxy.content_cache_stats()["size"] == 0

    @pytest.mark.parametrize(
        "cache_control,cached",
        [
            ("no-store", False),
            ("private, max-age=600", False),
            ("no-cache", False),
            ("public, max-age=600", True),
            ("", True),
        ],
    )
    def test_cache_control_honoured(self, db, tmp_path, cache_control, cached):
        proxy = _make_proxy(db, tmp_path)
        with patch.object(
            proxy, "_fetch_url", _mock_fetch(cache_control=cache_control)
        ), patch.object(
            proxy._url_validator,
            "validate_url_for_request",
            return_value=("example.com", "93.184.216.34"),
        ):
            proxy.get_content(_URL)
        assert (proxy.content_cache_stats()["size"] == 1) is cached

    def test_max_age_shortens_memory_ttl(self, db, tmp_path):
        proxy = _make_proxy(db, tmp_path, cache_ttl=86400)
        with patch.object(
            proxy, "_fetch_url", _mock_fetch(cache_control="public, max-age=0")
        ), patch.object(
            proxy._url_validator,
            "validate_url_for_request",
            return_value=("example.com", "93.184.216.34"),
        ):
            proxy.get_content(_URL)
            # max-age=0 → expires immediately → next call is a miss and refetches.
            proxy.get_content(_URL)
        assert proxy.content_cache_stats()["expired"] >= 1

    def test_invalidate_drops_memory_entry(self, db, tmp_path):
        proxy = _make_proxy(db, tmp_path)
        with patch.object(proxy, "_fetch_url", _mock_fetch()), patch.object(
            proxy._url_validator,
            "validate_url_for_request",
            return_value=("example.com", "93.184.216.34"),
        ):
            proxy.get_content(_URL)

        assert proxy.invalidate(_URL) is True
        assert proxy.content_cache_stats()["size"] == 0


class TestCacheBounds:
    def test_entry_and_byte_bounds_evict_lru(self):
        from src.core.media._proxy_cache import ProxyContentCache

        cache = ProxyContentCache(maxsize=2, max_bytes=1_000_000, ttl_seconds=60)
        cache.put("a", b"a" * 100, size=100)
        cache.put("b", b"b" * 100, size=100)
        cache.get("a")  # "a" is now most-recently-used
        cache.put("c", b"c" * 100, size=100)
        assert cache.get("b") is None  # LRU evicted
        assert cache.get("a") == b"a" * 100
        assert cache.get("c") == b"c" * 100
        assert cache.stats()["evictions"] == 1

    def test_single_oversized_value_not_cached(self):
        from src.core.media._proxy_cache import ProxyContentCache

        cache = ProxyContentCache(maxsize=10, max_bytes=50, ttl_seconds=60)
        cache.put("big", b"x" * 51, size=51)
        assert cache.get("big") is None
        assert cache.stats()["not_cached"] == 1
