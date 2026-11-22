"""Port-suffixed XFF hops must not let a client pick its own limiter bucket.

``get_client_ip`` strips an optional port from an X-Forwarded-For entry before
validating it and walking from the right. That stripping is security-critical,
not cosmetic: a trusted reverse proxy commonly appends the observed client as
``a.b.c.d:port`` (or ``[IPv6]:port``). If the port were left attached, the
appended entry would fail ``ipaddress`` parsing, the rightmost-untrusted-hop
walk would skip it, and the FIRST untrusted entry it did find would be the
attacker-controlled LEFTMOST hop — handing the caller an attacker-chosen IP
principal for rate limiting, IP bans and audit logs.

These tests pin the behaviour end to end (resolver -> limiter principal) and
are non-vacuous: a negative control shows a naive walk that does not strip the
port WOULD fall through to the attacker's entry on the very same input.
"""

import pytest

import utils.config as config
from src.core.ratelimit.manager.principals import normalise_ip_address
from src.utils.net import get_client_ip

pytestmark = pytest.mark.unit

ATTACKER = "6.6.6.6"
REAL_CLIENT = "198.51.100.42"
TRUSTED_PROXY = "10.0.0.1"


def _fake_config(monkeypatch, trusted):
    def fake_get(key, default=None):
        if key == "api":
            return {"trusted_proxies": trusted}
        return default if default is not None else {}

    monkeypatch.setattr(config, "get", fake_get)


def _scope(peer, headers):
    return {"client": (peer, 4000) if peer else None, "headers": list(headers)}


def _xff(value):
    return (b"x-forwarded-for", value.encode())


def _key(raw_ip):
    """The canonical IP principal the limiter actually keys on."""
    return normalise_ip_address(raw_ip)


@pytest.mark.parametrize(
    "appended,expected",
    [
        # Plain IPv4 client with an appended port: the proxy-observed hop.
        (f"{REAL_CLIENT}:5678", REAL_CLIENT),
        # Bracketed IPv6 client with a port.
        ("[2001:db8::42]:443", "2001:db8::42"),
        # No port still works (baseline).
        (REAL_CLIENT, REAL_CLIENT),
    ],
)
def test_proxy_appended_client_with_port_beats_attacker_leftmost(
    monkeypatch, appended, expected
):
    """The real client (rightmost, port-suffixed) wins over the forged left.

    The attacker prepends its own IP; the trusted proxy appends the observed
    client with a port. Skipping the appended hop would fall through to the
    attacker's entry and move the limiter key onto an attacker-chosen IP.
    """
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    scope = _scope(
        TRUSTED_PROXY,
        [_xff(f"{ATTACKER}, {appended}")],
    )

    raw = get_client_ip(scope)
    assert raw == expected
    assert _key(raw) == expected
    assert _key(raw) != ATTACKER


def test_attacker_leftmost_port_cannot_become_the_key(monkeypatch):
    """A port-suffixed attacker hop on the left never becomes the principal.

    Here the proxy appended the (portless) real client on the right: the walk
    must stop there and never reach the attacker's left entry, port or no port.
    """
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    scope = _scope(
        TRUSTED_PROXY,
        [_xff(f"{ATTACKER}:1234, {REAL_CLIENT}")],
    )

    raw = get_client_ip(scope)
    assert raw == REAL_CLIENT
    assert _key(raw) == REAL_CLIENT
    assert _key(raw) != ATTACKER


def test_port_suffix_on_trusted_proxy_hop_is_still_recognised(monkeypatch):
    """Port stripping also applies to the trusted check, so proxy hops skip.

    If the port were not stripped before the trusted-proxy match, a hop like
    ``10.0.0.9:8080`` would look untrusted and win as the client, allowing a
    downstream client to steer the key with a trusted-proxy-shaped value.
    """
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    scope = _scope(
        TRUSTED_PROXY,
        [_xff(f"10.0.0.9:8080, {REAL_CLIENT}")],
    )

    raw = get_client_ip(scope)
    assert raw == REAL_CLIENT


def test_negative_control_naive_walk_without_port_strip_would_be_spoofed(monkeypatch):
    """Non-vacuity: without port stripping the appended hop is skipped.

    A rightmost-walk that validates raw ``candidate`` strings (no port strip)
    rejects ``198.51.100.42:5678`` and falls through to the attacker's leftmost
    entry on the exact input above. The real resolver does not, so the guards
    above are meaningful.
    """
    _fake_config(monkeypatch, ["10.0.0.0/8"])

    def naive_no_strip(scope):
        parts = [
            p.strip()
            for k, v in scope["headers"]
            if k.lower() == b"x-forwarded-for"
            for p in v.decode("latin-1").split(",")
            if p.strip()
        ]
        import ipaddress

        for candidate in reversed(parts):
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
        return None

    hostile = _scope(
        TRUSTED_PROXY,
        [_xff(f"{ATTACKER}, {REAL_CLIENT}:5678")],
    )

    assert naive_no_strip(hostile) == ATTACKER  # regression the guard catches
    assert get_client_ip(hostile) == REAL_CLIENT  # current, correct behaviour
