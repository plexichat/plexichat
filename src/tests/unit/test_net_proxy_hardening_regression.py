"""Regression: trusted-proxy client-IP extraction must survive hostile headers.

Two weaknesses in ``src.utils.net.get_client_ip`` are pinned here:

1. Duplicate ``X-Forwarded-For`` headers. ASGI permits a header name more than
   once. Reading only the FIRST value let a client inject a spoofed XFF while a
   trusted reverse proxy appended the real client address in a LATER header.
   The rightmost-untrusted-hop walk then never saw the proxy-appended value and
   the attacker's IP was used for rate limiting, IP bans, audit logs and the
   admin host-restriction gate. Every XFF header must be treated as one list in
   wire order so the proxy's appended entry stays rightmost.

2. Non-UTF-8 bytes in a request header. The header decoder used
   ``bytes.decode()`` (UTF-8); a single invalid byte (e.g. ``0xff``) raised
   ``UnicodeDecodeError`` on this security-critical path. ASGI headers are
   latin-1, which never raises. A malformed header must degrade to a safe value,
   not crash the request (a cheap DoS / fail-open of every IP-based control).
"""

import utils.config as config
from src.utils.net import get_client_ip


def _fake_config(monkeypatch, trusted):
    def fake_get(key, default=None):
        if key == "api":
            return {"trusted_proxies": trusted}
        return default

    monkeypatch.setattr(config, "get", fake_get)


def _scope(client, raw_headers):
    return {"client": (client, 4000) if client else None, "headers": list(raw_headers)}


def test_duplicate_xff_spoof_in_first_header_is_ignored(monkeypatch):
    """A spoofed XFF in an earlier header must not win over the proxy's value."""
    _fake_config(monkeypatch, ["10.0.0.1"])
    # Attacker sends XFF: <fake> first; the trusted proxy appends a second XFF
    # with the address it actually observed. The real client is 198.51.100.42.
    scope = _scope(
        "10.0.0.1",
        [
            (b"x-forwarded-for", b"6.6.6.6"),
            (b"x-forwarded-for", b"198.51.100.42"),
        ],
    )
    assert get_client_ip(scope) == "198.51.100.42"


def test_duplicate_xff_hops_walk_from_last_header(monkeypatch):
    """All XFF headers form one logical list; the rightmost untrusted hop wins."""
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    scope = _scope(
        "10.0.0.1",
        [
            (b"x-forwarded-for", b"6.6.6.6"),
            (b"x-forwarded-for", b"198.51.100.42"),
        ],
    )
    # 6.6.6.6 is attacker-controlled (left); 198.51.100.42 was observed by the
    # proxy (right). The trusted-proxy hop 10.0.0.1 is the direct peer.
    assert get_client_ip(scope) == "198.51.100.42"


def test_duplicate_xff_all_trusted_hops_fall_back_to_peer(monkeypatch):
    """Multiple XFF headers that all name trusted hops fall back to the peer."""
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    scope = _scope(
        "10.0.0.1",
        [
            (b"x-forwarded-for", b"10.0.0.9"),
            (b"x-forwarded-for", b"10.0.0.8"),
        ],
    )
    assert get_client_ip(scope) == "10.0.0.1"


def test_malformed_utf8_xff_does_not_crash(monkeypatch):
    """A non-UTF-8 byte in XFF must degrade safely, never raise."""
    _fake_config(monkeypatch, ["10.0.0.1"])
    scope = _scope("10.0.0.1", [(b"x-forwarded-for", b"6.6.6.6\xff")])
    # The malformed entry is unparsable as an IP, so the walk ignores it and
    # falls back to the trusted direct peer rather than raising.
    assert get_client_ip(scope) == "10.0.0.1"


def test_malformed_utf8_on_untrusted_peer_is_ignored(monkeypatch):
    """A hostile header from an untrusted peer cannot crash IP extraction."""
    _fake_config(monkeypatch, [])
    scope = _scope(
        "203.0.113.5",
        [
            (b"x-forwarded-for", b"6.6.6.6\xff"),
            (b"cf-connecting-ip", b"9.9.9.9\xff"),
        ],
    )
    assert get_client_ip(scope) == "203.0.113.5"
