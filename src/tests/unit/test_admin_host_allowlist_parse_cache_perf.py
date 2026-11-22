"""PERF: the admin host allowlist must not be re-parsed on every request.

``check_host_restriction`` runs on every admin route.  The config-derived
``allowed_hosts`` list of IP/CIDR strings is shared, public data, but the old
implementation called ``ipaddress.ip_network`` / ``ipaddress.ip_address`` for
every entry on every single call.  A small bounded TTL cache keyed by the
allowlist contents removes that repeated CPU work without caching the authz
verdict (the verdict still depends on the per-request ``client_ip`` and is
recomputed on every call).
"""

import ipaddress

import pytest

from src.core import admin as core_admin

pytestmark = pytest.mark.unit

# A representative operator allowlist: two CIDRs and one exact host.
ALLOWED = ["198.51.100.0/24", "203.0.113.0/24", "192.0.2.7"]

# Peers that do NOT match any entry, so every entry is parsed on every call in
# the uncached implementation (no short-circuit).
MISSING_PEERS = ["8.8.8.8", "9.9.9.9"]


def _count_entry_parses(monkeypatch, allowed):
    """Count ipaddress parses of the *config allowlist* (not the client IP)."""
    real_network = ipaddress.ip_network
    real_address = ipaddress.ip_address
    calls = {"entry": 0}

    def counting_network(value, *args, **kwargs):
        if value in allowed:
            calls["entry"] += 1
        return real_network(value, *args, **kwargs)

    def counting_address(value, *args, **kwargs):
        if value in allowed:
            calls["entry"] += 1
        return real_address(value, *args, **kwargs)

    monkeypatch.setattr(ipaddress, "ip_network", counting_network)
    monkeypatch.setattr(ipaddress, "ip_address", counting_address)
    return calls


def _drop_cache():
    """Clear the parse cache when the fix is present; no-op before it."""
    invalidate = getattr(core_admin, "invalidate_host_rules_cache", None)
    if invalidate is not None:
        invalidate()


def test_allowlist_parsed_once_not_once_per_call(monkeypatch):
    """N calls parse the allowlist once (amortized), not N times.

    Before the fix this is N * len(MISSING_PEERS) * len(ALLOWED) parses.
    """
    calls = _count_entry_parses(monkeypatch, ALLOWED)
    _drop_cache()
    calls["entry"] = 0

    n = 40
    for _ in range(n):
        for peer in MISSING_PEERS:
            assert core_admin.check_host_restriction(peer, ALLOWED) is False

    parses = calls["entry"]
    uncached_baseline = n * len(MISSING_PEERS) * len(ALLOWED)
    # The allowlist is pure config data: it must be compiled once and reused.
    # A few parses are tolerated for cache construction; the per-call count
    # must collapse to a constant independent of N.
    assert parses <= len(ALLOWED) + 2, (
        f"allowlist parsed {parses} times across "
        f"{n * len(MISSING_PEERS)} calls (uncached baseline "
        f"{uncached_baseline}); expected a single amortized compile"
    )


# Reference implementation: the original, uncached semantics, copied verbatim
# so the test proves the cached path returns byte-for-byte identical verdicts.
def _reference_check_host_restriction(client_ip, allowed_hosts):
    if not allowed_hosts:
        return False
    localhost_variants = {"127.0.0.1", "localhost", "::1"}
    if client_ip in localhost_variants:
        return any(
            allowed in localhost_variants and client_ip in localhost_variants
            for allowed in allowed_hosts
        )
    try:
        client = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip in allowed_hosts
    for allowed in allowed_hosts:
        try:
            if "/" in allowed:
                if client in ipaddress.ip_network(allowed, strict=False):
                    return True
            elif client == ipaddress.ip_address(allowed):
                return True
        except ValueError:
            if client_ip == allowed:
                return True
    return False


@pytest.mark.parametrize(
    "client_ip, allowed",
    [
        ("198.51.100.5", ALLOWED),       # CIDR hit
        ("203.0.113.99", ALLOWED),       # second CIDR hit
        ("192.0.2.7", ALLOWED),          # exact host hit
        ("8.8.8.8", ALLOWED),            # miss
        ("127.0.0.1", ALLOWED),          # loopback peer, no loopback in list
        ("127.0.0.1", ["127.0.0.1"]),    # loopback peer + loopback list
        ("127.0.0.1", ["localhost"]),    # loopback variant in list
        ("203.0.113.10", []),            # empty allowlist -> fail closed
        ("not-an-ip", ALLOWED),          # malformed client -> literal membership
        ("not-an-ip", ["not-an-ip"]),    # malformed client literal hit
        ("198.51.100.5", ["not-a-net/xx", "198.51.100.0/24"]),  # bad entry fallback
    ],
)
def test_cached_path_output_identical(client_ip, allowed):
    _drop_cache()
    expected = _reference_check_host_restriction(client_ip, allowed)
    # Prime the cache, then read again to exercise the hit path.
    assert core_admin.check_host_restriction(client_ip, allowed) is expected
    assert core_admin.check_host_restriction(client_ip, allowed) is expected


def test_allowlist_mutation_does_not_cross_contaminate():
    """The cache key is the allowlist content, so edits cannot poison a verdict."""
    _drop_cache()
    first = ["198.51.100.0/24"]
    assert core_admin.check_host_restriction("198.51.100.5", first) is True
    poisoned = first + ["0.0.0.0/0"]
    assert core_admin.check_host_restriction("8.8.8.8", poisoned) is True
    # The original (pristine) allowlist still denies the outside peer.
    assert core_admin.check_host_restriction("8.8.8.8", first) is False


def test_parse_cache_is_bounded():
    """The cache has a fixed max size and a short TTL; it cannot grow freely."""
    get_stats = getattr(core_admin, "get_host_rules_cache_stats", None)
    if get_stats is None:
        pytest.skip("parse cache not yet present (pre-fix state)")
    _drop_cache()
    for i in range(200):
        core_admin.check_host_restriction("8.8.8.8", [f"10.{i}.0.0/16"])
    stats = get_stats()
    assert stats["max_entries"] >= 1 and stats["max_entries"] <= 128
    assert 0 < stats["ttl_seconds"] <= 60
    assert stats["size"] <= stats["max_entries"]
