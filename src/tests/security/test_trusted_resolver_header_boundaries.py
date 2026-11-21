"""Follow-up to f0d1a705: pin the remaining forwarding-header boundaries.

``get_client_ip`` deliberately consults only ``X-Forwarded-For`` (and
``CF-Connecting-IP`` as a fallback, gated by a trusted peer). Every other
forwarding hint -- RFC 7239 ``Forwarded``, ``X-Forwarded-Port``,
``True-Client-IP``, ``X-Real-IP``, ``X-Client-IP`` -- is intentionally ignored
so a caller cannot choose its own rate-limit / audit / ban key. That is a
fail-closed default, but it was only pinned in one weak form (a valid XFF was
always present, so the test would still pass if a future edit *honoured* those
headers at lower precedence).

These tests pin the stronger property: with no XFF/CF present, those headers
cannot move the key off the socket peer -- from an untrusted peer AND from a
trusted peer. RFC 7239 obfuscation shapes (``for=unknown``, nested quotes,
port injection) are covered explicitly.

They also close a real gap: ``ipaddress`` preserves an IPv6 zone/scope id
(``fe80::1%eth0``) in ``str()``, and a zone-bearing address can reach the
resolver from a client-controlled XFF entry or the ASGI socket peer. Left in
the principal, rotating the zone hands one source unbounded fresh buckets.
Normalization now strips the zone; the end-to-end tests below are non-vacuous
(the raw strings genuinely differ before normalization).

Run with::

    pytest src/tests/security/test_trusted_resolver_header_boundaries.py -q -n 0
"""

import pytest

import utils.config as config
from src.core.auth.managers.session.login import _canonical_source_ip
from src.core.ratelimit.manager.constants import MAX_COMPONENT_KEY_LENGTH
from src.core.ratelimit.manager.core import RateLimitManager
from src.core.ratelimit.manager.principals import normalise_ip_address
from src.core.ratelimit.models import RateLimitAlgorithm, RateLimitConfig
from src.utils.net import get_client_ip

pytestmark = pytest.mark.unit

ATTACKER = "6.6.6.6"
REAL_CLIENT = "198.51.100.42"
TRUSTED_PROXY = "10.0.0.1"

# Headers the resolver must NOT consult. Each value is the would-be spoof.
IGNORED_HEADERS = {
    "forwarded": f"for={ATTACKER}",
    "x-forwarded-port": "9999",
    "true-client-ip": ATTACKER,
    "x-real-ip": ATTACKER,
    "x-client-ip": ATTACKER,
}


def _fake_config(monkeypatch, trusted):
    def fake_get(key, default=None):
        if key == "api":
            return {"trusted_proxies": trusted}
        return default if default is not None else {}

    monkeypatch.setattr(config, "get", fake_get)


def _scope(client, headers):
    return {"client": (client, 4000) if client else None, "headers": list(headers)}


# --- 1. non-XFF/CF forwarding headers are ignored --------------------------


@pytest.mark.parametrize("name,value", list(IGNORED_HEADERS.items()))
@pytest.mark.parametrize(
    "peer,trusted",
    [
        ("203.0.113.9", [TRUSTED_PROXY]),  # direct, untrusted peer
        (TRUSTED_PROXY, [TRUSTED_PROXY]),  # trusted peer, but no XFF/CF
    ],
)
def test_forwarding_hint_cannot_set_key_without_xff_or_cf(
    monkeypatch, name, value, peer, trusted
):
    """With no XFF/CF, the key is the socket peer, never the hinted address."""
    _fake_config(monkeypatch, trusted)
    raw = get_client_ip(_scope(peer, [(name.encode(), value.encode())]))

    assert raw == peer
    assert normalise_ip_address(raw) == peer
    assert normalise_ip_address(raw) != ATTACKER


@pytest.mark.parametrize("name,value", list(IGNORED_HEADERS.items()))
def test_forwarding_hint_cannot_override_xff(monkeypatch, name, value):
    """Even alongside a valid XFF, no ignored hint may win."""
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    raw = get_client_ip(
        _scope(
            TRUSTED_PROXY,
            [
                (name.encode(), value.encode()),
                (b"x-forwarded-for", REAL_CLIENT.encode()),
            ],
        )
    )
    assert raw == REAL_CLIENT


@pytest.mark.parametrize(
    "rfc7239_value",
    [
        "for=unknown",
        'for="[2001:db8::1:1]"',  # nested quotes / bracketed IPv6 literal
        f'for="{ATTACKER}";proto=https;host=example.com',  # quoted + params
        f"for=192.0.2.1;for={ATTACKER}",  # duplicated for=
        f"{ATTACKER};proto=https",  # no element name at all
    ],
)
def test_rfc7239_obfuscation_shapes_are_ignored(monkeypatch, rfc7239_value):
    """RFC 7239 parsing/validation never runs -- the header is simply ignored."""
    _fake_config(monkeypatch, [TRUSTED_PROXY])
    raw = get_client_ip(_scope(TRUSTED_PROXY, [(b"forwarded", rfc7239_value.encode())]))
    assert raw == TRUSTED_PROXY
    assert normalise_ip_address(raw) != ATTACKER


def test_negative_control_naive_forwarding_resolver_would_be_spoofed():
    """Non-vacuity: a resolver that honoured these headers IS subvertible.

    Without this, an assertion that ``get_client_ip`` returns the peer could
    pass even if the resolver stopped being consulted at all.
    """
    scope = _scope("203.0.113.9", [(b"x-real-ip", ATTACKER.encode())])

    def naive(s):
        headers = dict((k.lower(), v.decode("latin-1")) for k, v in s["headers"])
        return headers.get(b"x-real-ip", s["client"][0])

    assert naive(scope) == ATTACKER  # the regression these guards catch
    assert get_client_ip(scope) == "203.0.113.9"  # current, correct behaviour


# --- 2. IPv6 zone / scope ids cannot multiply buckets ----------------------


# Same link-local address, varied zone/scope spelling -- all one host on the
# wire. The unzoned spelling is included so the test also covers the baseline.
ZONE_SPELLINGS = (
    "fe80::1%eth0",
    "fe80::1%eth1",
    "fe80::1%1",
    "fe80::1%wlan0",
    "fe80::1",
)


def test_zone_id_stripped_in_limiter_principal():
    assert {normalise_ip_address(s) for s in ZONE_SPELLINGS} == {"fe80::1"}
    # IPv4-mapped form is folded before any zone handling; still one principal.
    assert normalise_ip_address("::ffff:198.51.100.7%eth0") == "198.51.100.7"
    # Pre-existing bounds/control-char behaviour is unchanged.
    assert normalise_ip_address("x" * (MAX_COMPONENT_KEY_LENGTH + 1)) == ("invalid-ip")
    assert normalise_ip_address("bad\x00ip") == "invalid-ip"


def test_zone_id_from_trusted_xff_shares_one_key(monkeypatch):
    """Non-vacuous: raw XFF values differ, yet normalization yields one key."""
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    raws = [
        get_client_ip(_scope(TRUSTED_PROXY, [(b"x-forwarded-for", s.encode())]))
        for s in ZONE_SPELLINGS
    ]

    # The resolver surfaces the attacker-controlled zone verbatim ...
    assert len(set(raws)) > 1
    assert any("%" in r for r in raws)
    # ... but the limiter key canonicalizes every spelling to one principal.
    assert {normalise_ip_address(r) for r in raws} == {"fe80::1"}


def test_zone_rotation_cannot_grow_limiter_keyspace():
    cfg = RateLimitConfig(
        requests=2,
        window_seconds=60,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
    )
    manager = RateLimitManager(
        route_configs={"GET /t": cfg}, ip_config=cfg, enable_global_limit=False
    )
    results = [
        manager.check_rate_limit(ip_address=s, route="GET /t") for s in ZONE_SPELLINGS
    ]

    # One source, one budget: the 3rd request is denied no matter the zone.
    assert [r.allowed for r in results] == [True, True, False, False, False]
    assert len({r.bucket_key for r in results if not r.allowed}) == 1


def test_zone_bearing_socket_peer_shares_one_key(monkeypatch):
    """The ASGI peer may legitimately carry a zone; it must not fork buckets."""
    _fake_config(monkeypatch, [])
    raws = [get_client_ip(_scope(s, [])) for s in ZONE_SPELLINGS]

    assert len(set(raws)) > 1  # resolver preserves the infrastructure value
    assert {normalise_ip_address(r) for r in raws} == {"fe80::1"}


def test_auth_failure_budget_strips_zone_id():
    """Same root cause in the login failure-budget key (fed by the resolver)."""
    assert {_canonical_source_ip(s) for s in ZONE_SPELLINGS} == {"fe80::1"}
    assert _canonical_source_ip("::ffff:198.51.100.7%eth0") == "198.51.100.7"
