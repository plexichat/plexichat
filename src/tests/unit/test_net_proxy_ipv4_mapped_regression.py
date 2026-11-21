"""Regression: IPv4-mapped IPv6 must not break trusted-proxy recognition.

``::ffff:a.b.c.d`` is the same host as ``a.b.c.d``. ``_ip_matches_trusted``
used to compare them as different ``ipaddress`` families, so a dual-stack
reverse proxy reported as ``::ffff:127.0.0.1`` was *not* recognized when the
operator configured ``127.0.0.1`` as a trusted proxy. ``get_client_ip`` then
ignored X-Forwarded-For entirely and returned the proxy's loopback address:

* every client collapsed into one IP rate-limit bucket / audit identity;
* IP bans on real client addresses never matched;
* the admin proxy-spoof guard misread a proxied request as local loopback.

The fix normalizes mapped addresses to IPv4 on both sides of the trust check,
mirroring ``ip_validation.ip_in_rules``. These tests pin that behaviour without
relaxing any check: an untrusted mapped peer must still be refused.
"""

import pytest

import utils.config as config
from src.utils.net import _ip_matches_trusted, get_client_ip


def _scope(client, xff=None, headers=None):
    hdrs = list(headers or [])
    if xff is not None:
        hdrs.append((b"x-forwarded-for", xff.encode()))
    return {"client": (client, 4000) if client else None, "headers": hdrs}


def _fake_config(monkeypatch, trusted):
    def fake_get(key, default=None):
        if key == "api":
            return {"trusted_proxies": trusted}
        return default

    monkeypatch.setattr(config, "get", fake_get)


# ---------------------------------------------------------------------------
# _ip_matches_trusted: mapped and plain spellings decide identically
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("peer", "trusted", "expected"),
    [
        # Mapped candidate against plain IPv4 entry / CIDR.
        ("::ffff:127.0.0.1", ["127.0.0.1"], True),
        ("::ffff:10.1.2.3", ["10.0.0.0/8"], True),
        ("::ffff:192.168.1.9", ["192.168.1.0/24"], True),
        # Plain candidate against a mapped entry / mapped CIDR.
        ("10.1.2.3", ["::ffff:10.1.2.3"], True),
        ("10.1.2.3", ["::ffff:10.0.0.0/104"], True),
        # Genuine IPv6 is untouched by the normalization.
        ("2001:db8::1", ["2001:db8::/32"], True),
        ("::1", ["::1"], True),
        # A mapped address OUTSIDE the trusted range must still fail closed.
        ("::ffff:11.0.0.1", ["10.0.0.0/8"], False),
        ("::ffff:203.0.113.9", ["10.0.0.0/8"], False),
    ],
)
def test_mapped_trusted_proxy_matches(peer, trusted, expected):
    assert _ip_matches_trusted(peer, trusted) is expected


# ---------------------------------------------------------------------------
# get_client_ip: the observable impact of the bug
# ---------------------------------------------------------------------------


def test_mapped_trusted_peer_honors_xff(monkeypatch):
    """The proxy peer in mapped form must still yield the real client IP."""
    _fake_config(monkeypatch, ["127.0.0.1"])
    assert get_client_ip(_scope("::ffff:127.0.0.1", "203.0.113.7")) == "203.0.113.7"


def test_mapped_trusted_peer_cidr_honors_xff(monkeypatch):
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    assert get_client_ip(_scope("::ffff:10.0.0.1", "198.51.100.42")) == "198.51.100.42"


def test_mapped_untrusted_peer_ignores_xff(monkeypatch):
    """Hardening must not make a mapped, untrusted peer trusted."""
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    assert (
        get_client_ip(_scope("::ffff:203.0.113.9", "198.51.100.7"))
        == "::ffff:203.0.113.9"
    )


def test_mapped_trusted_hop_is_skipped_rightmost_untrusted_wins(monkeypatch):
    """Mapped proxy hops are skipped; attacker-controlled left entries stay ignored."""
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    # Rightmost hop is a trusted proxy in mapped form, so it is skipped;
    # the first untrusted hop walking left is the real client. The leftmost
    # "6.6.6.6" is attacker-controlled and must never be selected.
    xff = "6.6.6.6, ::ffff:10.0.0.9, 198.51.100.42"
    assert get_client_ip(_scope("10.0.0.1", xff)) == "198.51.100.42"


def test_mapped_all_hops_trusted_falls_back_to_peer(monkeypatch):
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    assert get_client_ip(_scope("10.0.0.1", "::ffff:10.0.0.9, 10.0.0.8")) == "10.0.0.1"
