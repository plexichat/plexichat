"""Regression tests for the bounded, short-TTL URL validation memo.

The memo must be a pure optimization: it may skip redundant DNS lookups for a
URL that was already fully validated, but it must never serve an unsafe result
or suppress a re-check once the (short) TTL lapses. These tests pin down the
SSRF-facing properties, not the hit-rate.
"""

from __future__ import annotations

import socket

import pytest

import src.utils.security as security
import src.utils.url_validation_cache as uvc
from src.utils.security import URLValidator, clear_url_resolution_cache
from src.utils.security import url_resolution_cache_stats


def _public_resolver(host, port, *a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def _resolver_for(*ips):
    def resolver(host, port, *a, **k):
        return [
            (
                socket.AF_INET6 if ":" in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (ip, 0),
            )
            for ip in ips
        ]

    return resolver


class FakeClock:
    """Controllable monotonic clock scoped to the cache module only."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch):
    # A fake clock keeps TTL tests instant and prevents wall-clock coupling.
    clock = FakeClock()
    monkeypatch.setattr(uvc, "time", clock)
    clear_url_resolution_cache()
    uvc.reset_url_validation_cache_stats()
    yield
    clear_url_resolution_cache()


# --- optimization: repeated lookups of the same URL collapse -----------------


def test_same_url_dns_is_memoized(monkeypatch):
    calls = {"n": 0}

    def counted(host, port, *a, **k):
        calls["n"] += 1
        return _public_resolver(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", counted)
    v = URLValidator()
    url = "https://cdn.example.com/a.png?size=1"

    first = v.validate_url_for_request(url)
    second = v.validate_url_for_request(url)

    assert first == second == ("cdn.example.com", "93.184.216.34")
    assert calls["n"] == 1
    assert url_resolution_cache_stats()["hits"] == 1


def test_key_is_full_url_not_host(monkeypatch):
    calls = {"n": 0}

    def counted(host, port, *a, **k):
        calls["n"] += 1
        return _public_resolver(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", counted)
    v = URLValidator()
    v.validate_url_for_request("https://cdn.example.com/a.png")
    v.validate_url_for_request("https://cdn.example.com/b.png?size=2")
    v.validate_url_for_request("http://cdn.example.com/a.png")
    assert calls["n"] == 3  # path, query and scheme all distinguish entries


def test_blocked_host_policy_partitions_cache(monkeypatch):
    calls = {"n": 0}

    def counted(host, port, *a, **k):
        calls["n"] += 1
        return _public_resolver(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", counted)
    url = "https://cdn.example.com/a"
    URLValidator().validate_url_for_request(url)
    assert calls["n"] == 1

    # A validator with a different blocklist cannot consume the other's entry.
    with pytest.raises(ValueError, match="forbidden"):
        URLValidator(blocked_hosts={"cdn.example.com"}).validate_url_for_request(url)
    assert calls["n"] == 1  # rejected before DNS, nothing cached/leaked


# --- negative results are never memoized ------------------------------------


@pytest.mark.parametrize(
    "resolver, match",
    [
        (_resolver_for("10.0.0.6"), "forbidden IP"),
        (_resolver_for("127.0.0.1"), "forbidden IP"),
        (_resolver_for("169.254.169.254"), "forbidden IP"),
        (_resolver_for("93.184.216.34", "10.0.0.6"), "forbidden IP"),
    ],
)
def test_private_resolutions_are_never_cached(monkeypatch, resolver, match):
    calls = {"n": 0}

    def counted(host, port, *a, **k):
        calls["n"] += 1
        return resolver(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", counted)
    v = URLValidator()
    url = "https://unsafe.example.com/x"
    for _ in range(3):
        with pytest.raises(ValueError, match=match):
            v.validate_url_for_request(url)
    # Every attempt re-resolves; a private answer is never served from cache.
    assert calls["n"] == 3
    assert url_resolution_cache_stats()["inserts"] == 0


def test_dns_failure_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def boom(host, port, *a, **k):
        calls["n"] += 1
        raise socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    v = URLValidator()
    for _ in range(2):
        with pytest.raises(ValueError, match="DNS resolution failed"):
            v.validate_url_for_request("https://missing.example.com/x")
    assert calls["n"] == 2


def test_empty_answer_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def empty(host, port, *a, **k):
        calls["n"] += 1
        return []

    monkeypatch.setattr(socket, "getaddrinfo", empty)
    v = URLValidator()
    for _ in range(2):
        with pytest.raises(ValueError):
            v.validate_url_for_request("https://empty.example.com/x")
    assert calls["n"] == 2


def test_host_that_turns_public_is_re_resolved(monkeypatch):
    """A previously-blocked host that later becomes public must be retried."""
    state = {"ips": ["10.0.0.6"]}
    calls = {"n": 0}

    def resolver(host, port, *a, **k):
        calls["n"] += 1
        return _resolver_for(*state["ips"])(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    v = URLValidator()
    url = "https://was-private.example.com/x"
    with pytest.raises(ValueError, match="forbidden IP"):
        v.validate_url_for_request(url)
    state["ips"] = ["93.184.216.34"]
    assert v.validate_url_for_request(url) == (
        "was-private.example.com",
        "93.184.216.34",
    )
    assert calls["n"] == 2


# --- DNS-rebinding / internal-IP rejection stay intact ----------------------


def test_no_ssrf_bypass_when_dns_flips_to_private(monkeypatch):
    """The core regression: a cached approval can never become an SSRF.

    The cached value is the previously-approved *public* IP, which callers pin
    to. When the name is rebound to a metadata address, the memo keeps handing
    back the public IP (never a private one); once the short TTL expires the
    name is re-resolved and the private answer is rejected outright.
    """
    state = {"ips": ["93.184.216.34"]}

    def resolver(host, port, *a, **k):
        return _resolver_for(*state["ips"])(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    v = URLValidator()
    url = "https://rebind.example.com/latest/meta-data"

    host, ip = v.validate_url_for_request(url)
    assert ip == "93.184.216.34"

    state["ips"] = ["169.254.169.254"]  # attacker rebinds to cloud metadata
    host, ip = v.validate_url_for_request(url)
    assert host == "rebind.example.com"
    assert ip == "93.184.216.34"
    assert not v.is_private_ip(ip)  # connection is pinned to a public IP

    # After the TTL the re-resolution policy re-checks DNS and refuses the
    # newly-private address instead of serving a stale approval.
    uvc.time.now += uvc.get_url_validation_cache().ttl + 1
    with pytest.raises(ValueError, match="forbidden IP"):
        v.validate_url_for_request(url)


def test_bounded_lru_evicts_oldest(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_resolver)
    cache = uvc.URLResolutionCache(maxsize=2, ttl_seconds=60)
    monkeypatch.setattr(security, "get_url_validation_cache", lambda: cache)

    v = URLValidator()
    v.validate_url_for_request("https://a.example/x")
    v.validate_url_for_request("https://b.example/x")
    stats = cache.stats()
    assert stats["size"] == 2
    v.validate_url_for_request("https://c.example/x")
    assert cache.stats()["size"] == 2
    assert cache.stats()["evictions"] == 1


def test_seltest_loopback_shortcut_is_not_cached(monkeypatch):
    monkeypatch.setenv("PLEXICHAT_SELF_TEST", "1")
    v = URLValidator()
    v.validate_url_for_request("http://127.0.0.1:9000/x")
    v.validate_url_for_request("http://localhost/x")
    assert url_resolution_cache_stats()["inserts"] == 0
