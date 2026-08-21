"""Tests for static-client request helpers (trusted-proxy-aware IP extraction)."""

from __future__ import annotations

import utils.config as config

from src.static_client.router import _parse_range, _scope_client_ip


def _scope(client=None, headers=None):
    return {"client": client, "headers": headers or []}


def _h(name, value):
    return (name.lower().encode(), value.encode())


def test_client_ip_direct_socket():
    scope = _scope(client=("203.0.113.7", 4000))
    assert _scope_client_ip(scope) == "203.0.113.7"


def test_xff_ignored_without_trusted_proxy():
    """X-Forwarded-For must be ignored when no trusted proxy is configured."""
    config.set("api", {"trusted_proxies": []})
    scope = _scope(
        client=("203.0.113.7", 4000),
        headers=[_h("x-forwarded-for", "1.2.3.4")],
    )
    assert _scope_client_ip(scope) == "203.0.113.7"


def test_xff_spoof_cannot_bypass_rate_limit():
    """A direct client claiming to sit behind a trusted proxy must not win."""
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    scope = _scope(
        client=("203.0.113.9", 4000),
        headers=[_h("x-forwarded-for", "10.0.0.5")],
    )
    assert _scope_client_ip(scope) == "203.0.113.9"


def test_xff_used_when_peer_trusted():
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    scope = _scope(
        client=("10.0.0.1", 4000),
        headers=[_h("x-forwarded-for", "198.51.100.7")],
    )
    assert _scope_client_ip(scope) == "198.51.100.7"


def test_no_client_returns_none():
    assert _scope_client_ip(_scope()) is None


# ---------------------------------------------------------------------------
# _parse_range (RFC 7233 byte-range handling)
# ---------------------------------------------------------------------------


def test_parse_range_suffix():
    assert _parse_range("-500", 1000) == (500, 999, True, False)
    # Suffix longer than the file yields the whole representation.
    assert _parse_range("-5000", 1000) == (0, 999, True, False)
    # A zero-length suffix is unsatisfiable.
    assert _parse_range("-0", 1000) == (0, 999, False, True)
    # Empty file: serve the (empty) full body.
    assert _parse_range("-100", 0) == (0, -1, False, False)


def test_parse_range_open_ended():
    assert _parse_range("100-", 1000) == (100, 999, True, False)
    assert _parse_range("0-", 1000) == (0, 999, True, False)


def test_parse_range_clamps_end():
    assert _parse_range("0-99999", 1000) == (0, 999, True, False)


def test_parse_range_unsatisfiable():
    # Start equals size -> no bytes in range.
    assert _parse_range("1000-", 1000) == (0, 999, False, True)
    # Inverted range.
    assert _parse_range("5-3", 1000) == (0, 999, False, True)
    # Malformed spec.
    assert _parse_range("abc", 1000) == (0, 999, False, True)


def test_parse_range_multi_range_falls_back_to_full():
    assert _parse_range("0-1,5-9", 1000) == (0, 999, False, False)
