"""Regression coverage for the bounded short-TTL link-preview resolution cache.

The persistent ``embed_preview_cache`` already absorbs repeat fetches when it
is warm, but a cold/stampeded cache still paid for the full remote metadata
resolution on every request. These tests pin the in-process tier that now
front-stops that work, together with the security invariants that must survive
the optimization:

* the SSRF/scheme gate still runs on every request, before the cache;
* rate limiting still applies per requesting user;
* entries are keyed by the full normalized URL and returned detached, so one
  user cannot observe or poison another user's resolution;
* TTL, entry count and estimated bytes are all bounded.
"""

from unittest.mock import Mock

import pytest

from src.core.embeds.link_preview import (
    LinkPreviewService,
    PreviewMetadata,
    ResolutionCache,
)


URL = "https://example.com/page"


def _miss_db():
    """DB whose persistent preview cache always misses."""
    db = Mock()
    db.fetch_one.return_value = None
    db.execute.return_value.rowcount = 1
    return db


def _service(db=None):
    service = LinkPreviewService(db or _miss_db())
    # The real SSRF gate is covered exhaustively in test_link_preview_security.
    # Here we stub just its network/DNS part and count its invocations.
    service._validate_url = Mock(
        side_effect=lambda url: ("example.com", "93.184.216.34", url)
    )
    service._fetch_with_redirects = Mock(
        side_effect=lambda url, hostname=None, resolved_ip=None: (
            b"<html><head><title>T</title></head></html>",
            url,
            "text/html",
        )
    )
    return service


class TestResolutionCachePerformance:
    def test_repeat_resolution_fetches_once_but_gate_runs_every_time(self):
        service = _service()

        for i in range(12):
            meta = service.generate_preview(user_id=100 + (i % 3), url=URL)
            assert meta.title == "T"

        # Fewer fetches: 20 -> 1 in the before/after benchmark.
        assert service._fetch_with_redirects.call_count == 1
        # SSRF gating is per-request and was NOT weakened by the cache.
        assert service._validate_url.call_count == 12
        stats = service.resolution_cache_stats()
        assert stats["hits"] == 11
        assert stats["misses"] == 1

    def test_skip_cache_forces_a_fresh_fetch_and_refreshes_the_tier(self):
        service = _service()
        titles = iter(["first", "second", "third"])
        service._parse_metadata = lambda html, base: PreviewMetadata(
            url=base, title=next(titles)
        )

        assert service.generate_preview(1, URL).title == "first"
        assert service.generate_preview(1, URL).title == "first"
        assert service._fetch_with_redirects.call_count == 1

        assert service.generate_preview(1, URL, skip_cache=True).title == "second"
        assert service._fetch_with_redirects.call_count == 2

        assert service.generate_preview(1, URL).title == "second"
        assert service._fetch_with_redirects.call_count == 2


class TestResolutionCacheSecurity:
    def test_ssrf_gate_still_runs_before_a_cache_hit(self):
        service = _service()
        assert service.generate_preview(1, URL).title == "T"

        # A cache hit must never short-circuit the gate for the new request.
        # Even though a valid entry exists, the now-rejecting gate wins.
        gate = service._validate_url
        # Replace the stub only after the first (successful) call.
        gate.side_effect = ValueError("blocked by SSRF gate")
        with pytest.raises(ValueError, match="blocked by SSRF gate"):
            service.generate_preview(2, URL)

        assert service._fetch_with_redirects.call_count == 1
        assert gate.call_count == 2

    def test_no_cross_user_leak(self):
        service = _service()
        consume = Mock(return_value=True)
        service._consume_rate_limit = consume

        upper = "https://example.com/Admin"
        lower = "https://example.com/admin"
        a = service.generate_preview(1, upper)
        service.generate_preview(2, lower)

        # Path case is significant: distinct resources must not share an entry.
        assert service._normalize_url(upper) != service._normalize_url(lower)
        assert service._fetch_with_redirects.call_count == 2

        # Per-user rate limiting is applied to cache hits too, keyed by the
        # actual requesting user (never a shared bucket).
        assert [call.args[0] for call in consume.call_args_list] == [1, 2]

        # A caller mutating its returned object cannot poison a later user's
        # hit: get() and put() both hand out detached copies.
        a.title = "ATTACKER"
        a.image_url = "http://169.254.169.254/latest/meta-data"
        again = service.generate_preview(3, upper)
        assert again.title == "T"
        assert again.image_url is None
        assert [call.args[0] for call in consume.call_args_list] == [1, 2, 3]
        assert service._fetch_with_redirects.call_count == 2

    def test_case_and_query_variants_are_separate_keys(self):
        service = _service()
        for url in (
            "https://EXAMPLE.com/page?Key=AbC",
            "https://example.com/page?Key=abc",
            "https://example.com/page?key=abc",
        ):
            service.generate_preview(1, url)
        assert service._fetch_with_redirects.call_count == 3


class TestResolutionCacheBounds:
    def test_short_ttl_expires_and_refetches(self):
        clock = {"now": 1000.0}
        service = _service()
        service._resolution_cache = ResolutionCache(
            ttl_seconds=5, max_entries=8, max_bytes=1 << 20, clock=lambda: clock["now"]
        )

        service.generate_preview(1, URL)
        clock["now"] += 4
        service.generate_preview(1, URL)
        assert service._fetch_with_redirects.call_count == 1

        clock["now"] += 2  # past the 5s TTL
        service.generate_preview(1, URL)
        assert service._fetch_with_redirects.call_count == 2
        assert service.resolution_cache_stats()["expirations"] == 1

    def test_entry_bound_evicts_least_recently_used(self):
        cache = ResolutionCache(ttl_seconds=60, max_entries=2, max_bytes=1 << 20)
        for i in range(5):
            cache.put(
                f"https://example.com/{i}",
                PreviewMetadata(url=f"https://example.com/{i}"),
                None,
            )

        stats = cache.stats()
        assert stats["size"] == 2
        assert stats["evictions"] == 3
        assert cache.get("https://example.com/0") is None
        assert cache.get("https://example.com/4") is not None

    def test_byte_bound_is_enforced(self):
        oversized = ResolutionCache(ttl_seconds=60, max_entries=100, max_bytes=150)
        oversized.put(URL, PreviewMetadata(url=URL, title="X" * 500), None)
        assert oversized.stats()["size"] == 0
        assert oversized.stats()["bytes"] == 0

        bounded = ResolutionCache(ttl_seconds=60, max_entries=100, max_bytes=260)
        for i in range(4):
            bounded.put(
                f"https://example.com/{i}",
                PreviewMetadata(url=f"https://example.com/{i}", title="x"),
                None,
            )
        stats = bounded.stats()
        assert 0 < stats["size"] <= stats["max_entries"]
        assert stats["bytes"] <= stats["max_bytes"]
        assert stats["evictions"] >= 1
