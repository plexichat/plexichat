"""Targeted tests for ``src.utils.net`` — trusted-proxy matching and client-IP extraction."""

import pytest

import utils.config as config
from src.utils.net import _ip_matches_trusted, get_client_ip


# ---------------------------------------------------------------------------
# _ip_matches_trusted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "proxies", "expected"),
    [
        # Exact IPv4 match
        ("192.168.1.1", ["192.168.1.1"], True),
        ("192.168.1.1", ["10.0.0.1", "192.168.1.1"], True),
        # Exact mismatch
        ("192.168.1.2", ["192.168.1.1"], False),
        # CIDR v4
        ("10.0.0.5", ["10.0.0.0/8"], True),
        ("172.16.3.9", ["172.16.0.0/12"], True),
        ("192.168.1.9", ["192.168.1.0/24"], True),
        ("192.168.2.1", ["192.168.1.0/24"], False),
        ("8.8.8.8", ["10.0.0.0/8"], False),
        # CIDR v6
        ("2001:db8::1", ["2001:db8::/32"], True),
        ("2001:db9::1", ["2001:db8::/32"], False),
        ("::1", ["::1/128"], True),
        # Hostname strings never match an IP entry
        ("localhost", ["127.0.0.1"], False),
        # Invalid entries are skipped, not fatal
        ("10.0.0.5", ["not-an-entry", "10.0.0.0/8"], True),
        ("10.0.0.5", ["10.0.0.0/999"], False),
        # Whitespace-tolerant entries
        ("10.0.0.5", [" 10.0.0.0/8 "], True),
        # Empty proxies list
        ("10.0.0.5", [], False),
        # Invalid IP string
        ("999.999.999.999", ["10.0.0.0/8"], False),
        ("", ["10.0.0.0/8"], False),
        # Mixed-family entries
        ("10.0.0.5", ["::1", "10.0.0.0/8"], True),
        # Non-strict network (host bits set in the network prefix)
        ("192.168.1.200", ["192.168.1.0/24"], True),
    ],
)
def test_ip_matches_trusted(ip, proxies, expected):
    assert _ip_matches_trusted(ip, proxies) is expected


def test_ip_matches_trusted_type_error_proxies():
    # Non-list containers should not raise (defensive).
    assert _ip_matches_trusted("10.0.0.5", ("10.0.0.0/8",)) is True


# ---------------------------------------------------------------------------
# get_client_ip (dict-scope mode)
# ---------------------------------------------------------------------------


def _scope(client=None, headers=None):
    return {"client": client, "headers": headers or []}


def _h(name, value):
    return (name.lower().encode(), value.encode())


def test_client_ip_direct():
    scope = _scope(client=("203.0.113.7", 4000))
    assert get_client_ip(scope) == "203.0.113.7"


def test_client_ip_no_client():
    assert get_client_ip(_scope()) is None


def test_client_ip_client_none():
    assert get_client_ip(_scope(client=None)) is None


def test_client_ip_ipv6_direct():
    scope = _scope(client=("2001:db8::5", 4000))
    assert get_client_ip(scope) == "2001:db8::5"


def test_forwarded_ignored_without_trusted_proxy():
    scope = _scope(
        client=("203.0.113.7", 4000),
        headers=[_h("x-forwarded-for", "1.2.3.4")],
    )
    # Trusted proxies come from config; default test config has none.
    assert get_client_ip(scope) == "203.0.113.7"


@pytest.mark.parametrize(
    "forwarded",
    [
        "10.0.0.5",
        "10.0.0.5, 203.0.113.9",
        " 10.0.0.5 , 203.0.113.9 ",
        "10.0.0.5,203.0.113.9,198.51.100.2",
    ],
)
def test_forwarded_used_when_peer_is_trusted(forwarded):
    # RIGHTMOST-UNTRUSTED-HOP selection: walk XFF from the right, skipping
    # entries inside the trusted proxy CIDR; the first untrusted address is
    # the client as observed by our own infrastructure.
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    scope = _scope(
        client=("10.0.0.1", 4000),
        headers=[_h("x-forwarded-for", forwarded)],
    )
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    untrusted = [
        p for p in reversed(parts) if not _ip_matches_trusted(p, ["10.0.0.0/8"])
    ]
    expected = untrusted[0] if untrusted else "10.0.0.1"
    assert get_client_ip(scope) == expected


def test_forwarded_all_hops_trusted_falls_back_to_peer():
    # Every reported hop is itself a trusted proxy -> fail safe to peer.
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    scope = _scope(
        client=("10.0.0.1", 4000),
        headers=[_h("x-forwarded-for", "10.0.0.5, 10.0.0.9")],
    )
    assert get_client_ip(scope) == "10.0.0.1"


def test_forwarded_attacker_injected_left_entries_ignored():
    # SECURITY: an attacker-controlled LEFTMOST entry must never win —
    # each request could otherwise land in a fresh IP bucket.
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    scope = _scope(
        client=("10.0.0.1", 4000),
        headers=[_h("x-forwarded-for", "6.6.6.6, 7.7.7.7")],
    )
    assert get_client_ip(scope) == "7.7.7.7"


def test_forwarded_used_with_exact_trusted_ip():
    config.set("api", {"trusted_proxies": ["10.0.0.1"]})
    scope = _scope(
        client=("10.0.0.1", 4000),
        headers=[_h("x-forwarded-for", "198.51.100.7")],
    )
    assert get_client_ip(scope) == "198.51.100.7"


def test_forwarded_first_part_is_client_not_last_proxy():
    # Standard behaviour: the left-most entry is the original client.
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    scope = _scope(
        client=("10.0.0.2", 4000),
        headers=[_h("x-forwarded-for", "198.51.100.3, 10.0.0.2, 10.0.0.1")],
    )
    assert get_client_ip(scope) == "198.51.100.3"


def test_forwarded_empty_parts_skipped():
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    scope = _scope(
        client=("10.0.0.1", 4000),
        headers=[_h("x-forwarded-for", " , 198.51.100.4 , ")],
    )
    assert get_client_ip(scope) == "198.51.100.4"


def test_forwarded_header_case_insensitive():
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    scope = _scope(
        client=("10.0.0.1", 4000),
        headers=[("X-FORWARDED-FOR".lower().encode(), b"198.51.100.9")],
    )
    assert get_client_ip(scope) == "198.51.100.9"


def test_trusted_proxy_config_missing_uses_empty():
    config.set("api", {})
    scope = _scope(
        client=("10.0.0.1", 4000),
        headers=[_h("x-forwarded-for", "198.51.100.9")],
    )
    assert get_client_ip(scope) == "10.0.0.1"


def test_trusted_proxy_config_none_value():
    config.set("api", {"trusted_proxies": None})
    scope = _scope(
        client=("10.0.0.1", 4000),
        headers=[_h("x-forwarded-for", "198.51.100.9")],
    )
    assert get_client_ip(scope) == "10.0.0.1"


def test_untrusted_proxy_cannot_spoof():
    # Attacker connects directly but claims to be a trusted proxy.
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    scope = _scope(
        client=("203.0.113.9", 4000),
        headers=[_h("x-forwarded-for", "10.0.0.5")],
    )
    assert get_client_ip(scope) == "203.0.113.9"


def test_mixed_header_names_ignored():
    scope = _scope(
        client=("203.0.113.7", 4000),
        headers=[_h("x-real-ip", "10.0.0.5")],
    )
    assert get_client_ip(scope) == "203.0.113.7"
