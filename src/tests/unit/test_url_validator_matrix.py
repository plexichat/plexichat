"""Dense matrix tests for ``URLValidator`` SSRF/DNS-rebinding protection.

IP classifications are pinned against the exact behavior of the
implementation (RFC1918 + loopback + link-local + reserved + multicast +
unspecified + CGNAT + documentation ranges).
"""

import socket

import pytest

from src.utils.security import URLValidator


# ---------------------------------------------------------------------------
# is_private_ip: exhaustive classification matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        # RFC1918 private IPv4
        ("10.0.0.1", True),
        ("10.255.255.255", True),
        ("172.16.0.1", True),
        ("172.31.255.255", True),
        ("192.168.1.1", True),
        ("192.168.255.255", True),
        # Outside RFC1918 (public)
        ("172.15.0.1", False),
        ("172.32.0.1", False),
        ("11.0.0.1", False),
        ("192.169.1.1", False),
        # Loopback
        ("127.0.0.1", True),
        ("127.255.255.255", True),
        # Unspecified / default route
        ("0.0.0.0", True),
        # Link-local
        ("169.254.0.1", True),
        ("169.254.255.255", True),
        # Multicast
        ("224.0.0.1", True),
        ("239.255.255.255", True),
        # Reserved / benchmarking
        ("240.0.0.1", True),
        ("255.255.255.255", True),
        ("198.18.0.1", True),
        ("198.19.255.255", True),
        # CGNAT (RFC 6598) — the SSRF gap this class exists to close
        ("100.64.0.1", True),
        ("100.127.255.255", True),
        ("100.63.255.255", False),
        ("100.128.0.1", False),
        # Documentation ranges (RFC 5737)
        ("192.0.2.1", True),
        ("198.51.100.3", True),
        ("203.0.113.7", True),
        # Genuinely public IPv4
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("9.9.9.9", False),
        ("208.67.222.222", False),
        ("93.184.216.34", False),
        ("192.0.1.1", False),
        # IPv6 private / reserved
        ("::1", True),
        ("::", True),
        ("fc00::1", True),
        ("fd12:3456:abcd::1", True),
        ("fe80::1", True),
        ("ff02::1", True),
        ("2001:db8::1", True),
        # IPv6 public
        ("2001:4860:4860::8888", False),
        ("2606:4700:4700::1111", False),
        ("2620:fe::fe", False),
        # IPv4-mapped IPv6 follows the embedded address
        ("::ffff:10.0.0.1", True),
        ("::ffff:127.0.0.1", True),
        ("::ffff:8.8.8.8", False),
        ("::ffff:100.64.0.1", True),  # mapped CGNAT is still blocked
        ("::ffff:100.127.255.255", True),
        ("::ffff:100.128.0.1", False),
        # Invalid / non-IP input is treated as private (conservative)
        ("not-an-ip", True),
        ("999.999.999.999", True),
        ("", True),
        ("10.0.0.1.5", True),
    ],
)
def test_is_private_ip_matrix(ip, expected):
    assert URLValidator().is_private_ip(ip) is expected


# ---------------------------------------------------------------------------
# URL scheme / structure rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<b>x</b>",
        "gopher://example.com",
        "ws://example.com",
        "wss://example.com",
        "mailto:user@example.com",
        "tel:+1234",
        "http://",
        "https://",
        "://example.com",
        "",
        "example.com/path",
    ],
)
def test_bad_scheme_or_missing_host_rejected(url):
    with pytest.raises(ValueError):
        URLValidator().validate_url_for_request(url)


# ---------------------------------------------------------------------------
# Blocked hostnames (case-insensitive + suffix rules + custom sets)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "LocalHost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "localhost.localdomain",
        "local",
        "example.local",
        "EXAMPLE.LOCAL",
        "db.internal",
        "api.internal",
    ],
)
def test_blocked_hostnames(host, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    with pytest.raises(ValueError):
        URLValidator().validate_url_for_request(f"http://{host}/x")


@pytest.mark.parametrize("host", ["forbidden.example.com", "FORBIDDEN.example.com"])
def test_custom_blocked_hosts_case_insensitive(host, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    v = URLValidator(blocked_hosts={"forbidden.example.com"})
    with pytest.raises(ValueError):
        v.validate_url_for_request(f"http://{host}/x")


def test_public_host_with_internal_suffix_allowed(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    host, ip = URLValidator().validate_url_for_request(
        "http://svc.internal.example.com/"
    )
    assert host == "svc.internal.example.com"
    assert ip == "93.184.216.34"


# ---------------------------------------------------------------------------
# DNS resolution safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resolved_ips",
    [
        ["10.0.0.6"],
        ["172.16.5.5"],
        ["192.168.0.1"],
        ["127.0.0.1"],
        ["169.254.0.1"],
        ["100.64.0.1"],
        ["::1"],
        ["fc00::1"],
        ["fe80::1"],
        ["ff02::1"],
        ["0.0.0.0"],
        ["93.184.216.34", "10.0.0.6"],  # mixed: any private IP must fail
        ["10.0.0.6", "93.184.216.34"],
    ],
)
def test_resolved_private_ip_rejected(monkeypatch, resolved_ips):
    def fake_getaddrinfo(host, port, *a, **k):
        return [
            (
                socket.AF_INET6 if ":" in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (ip, port),
            )
            for ip in resolved_ips
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="forbidden IP"):
        URLValidator().validate_url_for_request("http://example.com/")


def test_dns_failure_raises_clean_error(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(ValueError, match="DNS resolution failed"):
        URLValidator().validate_url_for_request("http://nonexistent.invalid/")


def test_empty_resolution_raises(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    with pytest.raises(ValueError):
        URLValidator().validate_url_for_request("http://example.com/")


# ---------------------------------------------------------------------------
# get_safe_url reconstruction
# ---------------------------------------------------------------------------


def _public_resolver(*a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


@pytest.mark.parametrize(
    ("url", "expected_safe"),
    [
        ("http://example.com/path", "http://93.184.216.34/path"),
        ("https://example.com/path", "https://93.184.216.34/path"),
        ("http://example.com:8080/a", "http://93.184.216.34:8080/a"),
        ("http://example.com/a?b=c", "http://93.184.216.34/a?b=c"),
        ("http://example.com/a#frag", "http://93.184.216.34/a#frag"),
        ("http://example.com/a?b=c#frag", "http://93.184.216.34/a?b=c#frag"),
        ("http://example.com:443/", "http://93.184.216.34:443/"),
    ],
)
def test_get_safe_url_matrix(monkeypatch, url, expected_safe):
    monkeypatch.setattr(socket, "getaddrinfo", _public_resolver)
    safe, host, ip = URLValidator().get_safe_url(url)
    assert safe == expected_safe
    assert host == "example.com"
    assert ip == "93.184.216.34"
