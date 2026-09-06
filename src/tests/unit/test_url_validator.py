"""Targeted tests for ``src.utils.security.URLValidator`` (SSRF/DNS-rebinding protection)."""

import socket

import pytest

from src.utils.security import URLValidator, generate_csp_nonce, build_admin_csp_header


# ---------------------------------------------------------------------------
# is_private_ip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        # Private / reserved IPv4
        ("10.0.0.1", True),
        ("10.255.255.255", True),
        ("172.16.0.1", True),
        ("172.31.255.255", True),
        ("192.168.1.1", True),
        ("127.0.0.1", True),
        ("0.0.0.0", True),
        ("169.254.1.1", True),
        ("224.0.0.1", True),
        ("240.0.0.1", True),
        ("100.64.0.1", True),
        # Public IPv4
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        # RFC 5737 documentation ranges: non-routable, treated as blocked
        ("203.0.113.7", True),
        ("198.51.100.3", True),
        # Private / reserved IPv6
        ("::1", True),
        ("::", True),
        ("fc00::1", True),
        ("fd12:3456::1", True),
        ("fe80::1", True),
        ("ff02::1", True),
        # Public IPv6
        ("2001:4860:4860::8888", False),
        ("2606:4700:4700::1111", False),
        # Invalid / non-IP input is treated as private (conservative)
        ("not-an-ip", True),
        ("999.999.999.999", True),
        ("", True),
    ],
)
def test_is_private_ip(ip, expected):
    assert URLValidator().is_private_ip(ip) is expected


# ---------------------------------------------------------------------------
# validate_url_for_request
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
        "",
        "example.com/path",  # no scheme
        "http://",  # no hostname
        "://example.com",
    ],
)
def test_rejects_bad_scheme_or_missing_host(url):
    with pytest.raises(ValueError):
        URLValidator().validate_url_for_request(url)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "localhost.localdomain",
        "local",
        "example.local",
        "db.internal",
    ],
)
def test_rejects_blocked_and_internal_hosts(host, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    with pytest.raises(ValueError):
        URLValidator().validate_url_for_request(f"http://{host}/path")


def test_rejects_custom_blocked_host(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    v = URLValidator(blocked_hosts={"forbidden.example.com"})
    with pytest.raises(ValueError):
        v.validate_url_for_request("http://forbidden.example.com/x")


def test_internal_mid_label_host_allowed(monkeypatch):
    # "internal" as a middle label (not the TLD) is a public-looking
    # hostname and must be allowed when it resolves publicly.
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    host, ip = URLValidator().validate_url_for_request(
        "http://api.internal.example.com/x"
    )
    assert host == "api.internal.example.com"
    assert ip == "93.184.216.34"


def test_scheme_case_insensitive_allowed(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    host, ip = URLValidator().validate_url_for_request("HTTP://example.com/a")
    assert host == "example.com"
    assert ip == "93.184.216.34"


def test_dns_resolution_failure_raises(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(ValueError, match="DNS resolution failed"):
        URLValidator().validate_url_for_request("http://nonexistent.invalid/")


def test_empty_addr_info_raises(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    with pytest.raises(ValueError):
        URLValidator().validate_url_for_request("http://example.com/")


@pytest.mark.parametrize(
    ("resolved_ips", "match"),
    [
        ([("10.0.0.6", 0)], "forbidden IP"),
        ([("127.0.0.1", 0)], "forbidden IP"),
        ([("169.254.0.1", 0)], "forbidden IP"),
        ([("::1", 0)], "forbidden IP"),
        ([("fc00::1", 0)], "forbidden IP"),
        ([("93.184.216.34", 0), ("10.0.0.6", 0)], "forbidden IP"),
    ],
)
def test_resolved_private_ips_rejected(monkeypatch, resolved_ips, match):
    def fake_getaddrinfo(host, port, *a, **k):
        return [
            (
                socket.AF_INET6 if ":" in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (ip, port),
            )
            for ip, _ in resolved_ips
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match=match):
        URLValidator().validate_url_for_request("http://example.com/")


def test_public_resolution_accepted(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    host, ip = URLValidator().validate_url_for_request("http://example.com/x?y=1")
    assert host == "example.com"
    assert ip == "93.184.216.34"


def test_hostname_with_port_ok(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    host, ip = URLValidator().validate_url_for_request("http://example.com:8080/x")
    assert host == "example.com"


# ---------------------------------------------------------------------------
# get_safe_url
# ---------------------------------------------------------------------------


def test_get_safe_url_with_port(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    safe, host, ip = URLValidator().get_safe_url("http://example.com:8080/a?b=c#frag")
    assert safe == "http://93.184.216.34:8080/a?b=c#frag"
    assert host == "example.com"
    assert ip == "93.184.216.34"


def test_get_safe_url_no_port(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    safe, host, _ = URLValidator().get_safe_url("https://example.com/path")
    assert safe == "https://93.184.216.34/path"
    assert host == "example.com"


def test_get_safe_url_rejects_private(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))],
    )
    with pytest.raises(ValueError):
        URLValidator().get_safe_url("http://example.com/")


# ---------------------------------------------------------------------------
# CSP helpers
# ---------------------------------------------------------------------------


def test_generate_csp_nonce_unique_and_base64():
    n1 = generate_csp_nonce()
    n2 = generate_csp_nonce()
    assert n1 != n2
    import base64

    assert len(base64.b64decode(n1)) == 16


def test_build_admin_csp_header_contains_nonce_and_restrictions():
    header = build_admin_csp_header("abc123")
    assert "nonce-abc123" in header
    assert "default-src 'self'" in header
    assert "frame-ancestors 'none'" in header
    assert "base-uri 'self'" in header
    assert "form-action 'self'" in header
    assert "script-src" in header


def test_csp_header_differs_with_nonce():
    assert build_admin_csp_header("a") != build_admin_csp_header("b")


# ---------------------------------------------------------------------------
# PLEXICHAT_SELF_TEST loopback allowance (fake remote federation peer)
# ---------------------------------------------------------------------------


def test_loopback_blocked_without_selftest_env(monkeypatch):
    monkeypatch.delenv("PLEXICHAT_SELF_TEST", raising=False)
    for url in (
        "https://127.0.0.1:8443/x",
        "https://localhost/x",
        "https://[::1]/x",
    ):
        with pytest.raises(ValueError):
            URLValidator().validate_url_for_request(url)


def test_loopback_allowed_with_selftest_env(monkeypatch):
    monkeypatch.setenv("PLEXICHAT_SELF_TEST", "1")
    host, ip = URLValidator().validate_url_for_request("https://127.0.0.1:8443/x")
    assert host == "127.0.0.1"
    assert ip == "127.0.0.1"
    host, ip = URLValidator().validate_url_for_request("https://localhost/x")
    assert ip == "127.0.0.1"
    host, ip = URLValidator().validate_url_for_request("https://[::1]/x")
    assert ip == "::1"


def test_selftest_env_does_not_allow_non_loopback(monkeypatch):
    # The allowance is loopback-literals only: any other host still goes
    # through DNS + private-IP rejection even with the env var set.
    monkeypatch.setenv("PLEXICHAT_SELF_TEST", "1")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
    )
    with pytest.raises(ValueError, match="forbidden IP"):
        URLValidator().validate_url_for_request("https://internal.example.com/x")
