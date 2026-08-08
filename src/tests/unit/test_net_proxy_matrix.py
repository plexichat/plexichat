"""Dense matrix tests for trusted-proxy matching and client-IP extraction
(``src.utils.net``).

Covers X-Forwarded-For spoofing resistance, CIDR trust, and malformed
header handling.
"""

import pytest

import utils.config as config
from src.utils.net import _ip_matches_trusted, get_client_ip


# ---------------------------------------------------------------------------
# _ip_matches_trusted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "proxies", "expected"),
    [
        ("10.0.0.5", ["10.0.0.5"], True),
        ("10.0.0.5", ["10.0.0.6"], False),
        ("10.0.0.5", ["10.0.0.0/8"], True),
        ("10.9.9.9", ["10.0.0.0/8"], True),
        ("11.0.0.1", ["10.0.0.0/8"], False),
        ("10.0.0.5", ["10.0.0.0/24"], True),
        ("10.0.1.5", ["10.0.0.0/24"], False),
        ("2001:db8::1", ["2001:db8::/32"], True),
        ("2001:db8::1", ["2001:db9::/32"], False),
        ("2001:db8::1", ["2001:db8::1"], True),
        ("10.0.0.5", [" 10.0.0.5 "], True),  # whitespace stripped
        ("10.0.0.5", ["10.0.0.0/8 ", "bad"], True),  # invalid entries skipped
        ("10.0.0.5", ["not-an-ip"], False),
        ("10.0.0.5", [""], False),
        ("10.0.0.5", [], False),
        ("10.0.0.5", ["10.0.0.0/33"], False),  # invalid CIDR skipped
        ("bad-ip", ["10.0.0.5"], False),
        ("", ["10.0.0.5"], False),
    ],
)
def test_ip_matches_trusted_matrix(ip, proxies, expected):
    assert _ip_matches_trusted(ip, proxies) is expected


# ---------------------------------------------------------------------------
# get_client_ip: scope-based extraction
# ---------------------------------------------------------------------------


def _scope(client, xff=None):
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    return {"client": client, "headers": headers}


@pytest.mark.parametrize(
    ("client", "xff", "trusted", "expected"),
    [
        # No proxy headers at all
        (("8.8.8.8", 1234), None, [], "8.8.8.8"),
        # No client socket
        (None, None, [], None),
        # XFF ignored when direct peer is not trusted
        (("1.2.3.4", 1234), "9.9.9.9", [], "1.2.3.4"),
        (("1.2.3.4", 1234), "9.9.9.9", ["5.6.7.8"], "1.2.3.4"),
        # XFF honored when direct peer is trusted (exact)
        (("10.0.0.5", 1234), "9.9.9.9", ["10.0.0.5"], "9.9.9.9"),
        # Trusted via CIDR
        (("10.1.2.3", 1234), "8.8.8.8", ["10.0.0.0/8"], "8.8.8.8"),
        (("172.16.0.1", 1234), "8.8.8.8", ["172.16.0.0/12"], "8.8.8.8"),
        # Multi-hop XFF: first entry is the original client
        (("10.0.0.5", 1234), "9.9.9.9, 8.8.8.8, 7.7.7.7", ["10.0.0.5"], "9.9.9.9"),
        (("10.0.0.5", 1234), "9.9.9.9, ", ["10.0.0.5"], "9.9.9.9"),
        # Blank XFF -> parts empty -> direct peer used
        (("10.0.0.5", 1234), "   ", ["10.0.0.5"], "10.0.0.5"),
        # Header case-insensitive
        (("10.0.0.5", 1234), "1.1.1.1", ["10.0.0.5"], "1.1.1.1"),
        # IPv6 direct peer
        (("2001:db8::1", 1234), None, [], "2001:db8::1"),
    ],
)
def test_get_client_ip_matrix(monkeypatch, client, xff, trusted, expected):
    def fake_get(key, default=None):
        if key == "api":
            return {"trusted_proxies": trusted}
        return default

    monkeypatch.setattr(config, "get", fake_get)
    assert get_client_ip(_scope(client, xff)) == expected


def test_trusted_proxy_config_error_falls_back_to_direct(monkeypatch):
    def boom(key, default=None):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(config, "get", boom)
    assert get_client_ip(_scope(("8.8.8.8", 1), "9.9.9.9")) == "8.8.8.8"


def test_empty_trusted_proxy_list(monkeypatch):
    def fake_get(key, default=None):
        if key == "api":
            return {"trusted_proxies": None}
        return default

    monkeypatch.setattr(config, "get", fake_get)
    assert get_client_ip(_scope(("1.2.3.4", 1), "9.9.9.9")) == "1.2.3.4"
