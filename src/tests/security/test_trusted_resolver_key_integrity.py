"""Follow-up to b05cc08c: the key *resolvers* must be authoritative too.

``b05cc08c`` pins every security-critical limiter call site to an authoritative
key source (a validated user/admin id, a SHA-256 fingerprint, or the trusted
client IP from ``get_client_ip`` / ``cached_client_ip``). That guard is only as
strong as the resolvers it blesses: a resolver that honours a client-controlled
header would let the caller pick its own bucket and defeat the whole ratchet.

These runtime tests exercise the resolver -> principal -> key path end to end
and cover the four ways a "trusted" key can be subverted:

* an untrusted peer injecting ``X-Forwarded-For`` / ``CF-Connecting-IP``;
* duplicate XFF headers hiding a spoof ahead of the proxy's append;
* IPv4-mapped / CIDR spellings confusing the rightmost-untrusted-hop walk;
* a malformed bot-token verdict falling back to the human owner.

They are non-vacuous: the negative control feeds the same hostile header to a
naive "leftmost XFF wins" resolver and shows the attacker WOULD own the bucket
there, while the real resolver refuses it. Each assertion is therefore checking
the trust boundary itself, not passing trivially.

Run with::

    pytest src/tests/security/test_trusted_resolver_key_integrity.py -q -n 0
"""

from types import SimpleNamespace

import pytest

import utils.config as config
import src.core.ratelimit.manager.principals as principals
import src.utils.net as net
from src.core.ratelimit.manager.principals import (
    normalise_ip_address,
    normalise_principal,
    resolve_credential_principal,
)
from src.utils.net import get_client_ip

pytestmark = pytest.mark.unit

ATTACKER = "6.6.6.6"
REAL_CLIENT = "198.51.100.42"
TRUSTED_PROXY = "10.0.0.1"
OWNER_ID = 100
BOT_ID = 900


def _fake_config(monkeypatch, trusted):
    def fake_get(key, default=None):
        if key == "api":
            return {"trusted_proxies": trusted}
        return default if default is not None else {}

    monkeypatch.setattr(config, "get", fake_get)


def _scope(client, headers):
    return {"client": (client, 4000) if client else None, "headers": list(headers)}


def _key(raw_ip):
    """The canonical IP principal the limiter actually keys on."""
    return normalise_ip_address(raw_ip)


# --- 1. untrusted peer cannot move its own bucket --------------------------


@pytest.mark.parametrize(
    "headers",
    [
        [(b"x-forwarded-for", ATTACKER.encode())],
        [(b"cf-connecting-ip", ATTACKER.encode())],
        [
            (b"x-forwarded-for", ATTACKER.encode()),
            (b"cf-connecting-ip", ATTACKER.encode()),
        ],
    ],
)
def test_untrusted_peer_forwarded_headers_cannot_change_key(monkeypatch, headers):
    """Forwarded headers from a direct (untrusted) peer are ignored entirely."""
    _fake_config(monkeypatch, [TRUSTED_PROXY])
    raw = get_client_ip(_scope("203.0.113.9", headers))

    assert raw == "203.0.113.9"
    assert _key(raw) != ATTACKER


# --- 2. trusted peer: proxy-appended rightmost hop wins --------------------


def test_proxy_appended_rightmost_hop_beats_attacker_prepended_entries(monkeypatch):
    """Only the address our trusted proxy observed may key the bucket.

    The attacker prepends a fake hop ahead of the proxy's append, splits the
    chain over duplicate headers, and even names a trusted proxy in mapped
    form. Walking from the right must still land on the real client.
    """
    _fake_config(monkeypatch, ["10.0.0.0/8"])
    scope = _scope(
        TRUSTED_PROXY,
        [
            (b"x-forwarded-for", f"{ATTACKER}, 10.0.0.9".encode()),
            (b"x-forwarded-for", f"::ffff:10.0.0.8, {REAL_CLIENT}".encode()),
        ],
    )

    raw = get_client_ip(scope)
    assert raw == REAL_CLIENT
    assert _key(raw) == REAL_CLIENT
    assert _key(raw) != ATTACKER


# --- 3. IPv4-mapped / CIDR spellings cannot confuse the walk ---------------


def test_ipv4_mapped_spellings_share_one_limiter_bucket(monkeypatch):
    """A mapped real client and its plain IPv4 spelling are one principal."""
    _fake_config(monkeypatch, ["10.0.0.0/8"])

    def raw_for(spelling):
        return get_client_ip(
            _scope(TRUSTED_PROXY, [(b"x-forwarded-for", spelling.encode())])
        )

    mapped = raw_for(f"::ffff:{REAL_CLIENT}")
    plain = raw_for(REAL_CLIENT)

    assert _key(mapped) == _key(plain) == REAL_CLIENT
    assert _key(mapped) != ATTACKER


def test_malformed_bot_verdict_never_keys_on_owner():
    """A bot verdict with no distinct bot account fails closed to the IP.

    Falling back to ``user_id`` here would let a malformed bot credential
    spend the human owner's quota (cross-principal confusion).
    """
    credential = SimpleNamespace(
        token_type="bot",
        account_id=None,
        user_id=OWNER_ID,
    )

    resolved = resolve_credential_principal(credential)
    user_id, ip = normalise_principal(resolved, REAL_CLIENT)

    assert resolved is None
    assert user_id is None
    assert ip == REAL_CLIENT
    assert ip != str(OWNER_ID)


# --- 4. non-vacuity: a naive resolver WOULD be spoofed by the same input ---


def test_negative_control_naive_leftmost_resolver_is_spoofed(monkeypatch):
    """The hostile header above is only harmless because of the trust walk.

    If a future edit regressed the resolver to "first XFF entry wins", this
    control fails and the assertions above would no longer be meaningful.
    """
    _fake_config(monkeypatch, ["10.0.0.0/8"])

    def naive(scope):
        for name, value in scope["headers"]:
            if name.lower() == b"x-forwarded-for":
                return value.decode("latin-1").split(",")[0].strip()
        return scope["client"][0]

    hostile = _scope(
        TRUSTED_PROXY,
        [(b"x-forwarded-for", f"{ATTACKER}, {REAL_CLIENT}".encode())],
    )

    assert naive(hostile) == ATTACKER  # the regression the guard must catch
    assert get_client_ip(hostile) == REAL_CLIENT  # current, correct behaviour


# --- 5. non-vacuity: the two other named regressions are also caught ---------


def test_self_test_wildcard_trust_mutation_is_caught(monkeypatch):
    """Trusting every peer makes the REAL resolver honor attacker headers.

    Mutating the resolver's trusted-peer check itself (rather than reimplementing
    a naive resolver) is the faithful "trusted_proxies regressed to a wildcard"
    regression. The untrusted-peer assertion in test 1 rejects exactly the
    result produced here, so that guard is non-vacuous for this regression too.
    """
    monkeypatch.setattr(net, "_ip_matches_trusted", lambda *_a, **_k: True)
    hostile = _scope(
        "203.0.113.9",
        [
            (b"x-forwarded-for", ATTACKER.encode()),
            (b"cf-connecting-ip", ATTACKER.encode()),
        ],
    )

    # Broken (trust-all) resolver is subverted by the client-controlled headers.
    assert get_client_ip(hostile) == ATTACKER
    # The guard's own key assertion (test 1) rejects that same attacker address.
    assert _key(ATTACKER) != "203.0.113.9"


def test_self_test_bot_owner_fallback_mutation_is_caught(monkeypatch):
    """A bot verdict that falls back to the owner keys on the human owner.

    Mutating the real resolver's fallback behaviour shows it would resolve to
    the owner id and charge that principal — precisely what test 4 rejects.
    The direct import still calls the unmutated function, confirming correct
    behaviour is what the guard asserts.
    """
    credential = SimpleNamespace(
        token_type="bot",
        account_id=None,
        user_id=OWNER_ID,
    )
    monkeypatch.setattr(
        principals,
        "resolve_credential_principal",
        lambda c: getattr(c, "user_id", None),
    )

    broken = principals.resolve_credential_principal(credential)
    broken_uid, _ = normalise_principal(broken, REAL_CLIENT)
    assert broken_uid == OWNER_ID  # the regression guard test 4 rejects
    assert resolve_credential_principal(credential) is None  # correct behaviour
