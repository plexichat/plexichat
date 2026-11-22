"""API-token verification derives each binding blind index once per request.

``verify_api_access_token`` runs on every authenticated request. Its per-request
side effects (which deliberately stay outside the introspection cache) update
the ``last_used_*`` columns and write a usage event. Both sinks need the SAME
two invariants for a given (IP, User-Agent): the IP blind index (scope
``ip_address``) and the UA blind index (scope ``user_agent``).

Before the fix each was derived twice per verified request -- once inline for
the UPDATE and again inside ``_record_api_access_token_event``. That is pure
waste on the hot path (two extra keyed BLAKE2b hashes per request), so hoist the
invariant and thread the already-derived values into the event writer.

Credential scoping is unchanged: the *encrypted* values still bind to distinct
AAD contexts (token id for the UPDATE, event id for the INSERT); only the
deterministic, context-free blind indexes are reused. Fail-closed verdict
checks and strict parsing are untouched.

Run with::

    pytest src/tests/auth/test_api_token_verify_index_recompute_hotpath.py -q -n 0
"""

import hashlib

import pytest

import src.core.auth.managers.api_token as api_token
from src.core.auth.managers.api_token import ApiTokenMixin, _api_token_verify_key

pytestmark = pytest.mark.unit

_IP = "198.51.100.7"
_UA = "regression-agent/1.0"
_TOKEN = "a" * 40


class _CountingCrypto:
    """Crypto stub that counts per-scope blind-index derivations."""

    def __init__(self):
        self.ip_index_calls = 0
        self.blind_index_calls = 0

    def fast_blind_index(self, data, scope):
        if scope == "ip_address":
            self.ip_index_calls += 1
        return hashlib.sha256(f"{scope}:{data.strip().lower()}".encode()).hexdigest()

    def blind_index(self, data, scope):
        if scope == "user_agent":
            self.blind_index_calls += 1
        return hashlib.sha256(f"{scope}:{data.strip().lower()}".encode()).hexdigest()

    def encrypt_data(self, data, context=None):
        return f"enc:{context}:{data}"


class _DB:
    def __init__(self):
        self.executes = []

    def fetch_one(self, sql, params):
        # Satisfies the cache-hit authoritative-recheck (a live, unrevoked row).
        return {"revoked": 0}

    def execute(self, sql, params):
        self.executes.append((sql, tuple(params)))


def _mixin():
    m = object.__new__(ApiTokenMixin)
    m._db = _DB()
    m.crypto = _CountingCrypto()
    m._get_timestamp = lambda: 1_000_000
    m._generate_id = lambda: 4242
    m._ua_calls = 0

    def _ua_index(ua):
        m._ua_calls += 1
        return hashlib.sha256(f"ua:{ua}".encode()).hexdigest() if ua else None

    m._ua_index = _ua_index
    m._encrypt_ua = lambda ua, ctx: f"enc-ua:{ctx}:{ua}" if ua else None
    return m


def _verdict():
    return {
        "authentic": True,
        "token_id": 7,
        "expires_at": None,
        "scope_match": True,
        "has_scopes": False,
        "scope_mode": "none",
        "last_used_at": 0,
        "first_used_at": 1,  # skip the extra first_used_at assignment
    }


def _run():
    """One successful, cache-hit verification with a bound IP + UA."""
    m = _mixin()
    key = _api_token_verify_key(_TOKEN, _IP, _UA)
    api_token._API_TOKEN_VERIFY_CACHE.set(key, _verdict())
    try:
        assert m.verify_api_access_token(_TOKEN, ip_address=_IP, user_agent=_UA) is True
    finally:
        api_token._API_TOKEN_VERIFY_CACHE.clear()
    return m


def test_ip_blind_index_derived_once_per_verified_request():
    m = _run()
    assert m.crypto.ip_index_calls == 1, (
        f"expected 1 ip_address blind index derivation, got {m.crypto.ip_index_calls}"
    )


def test_ua_blind_index_derived_once_per_verified_request():
    m = _run()
    assert m._ua_calls == 1, f"expected 1 UA blind index derivation, got {m._ua_calls}"


def test_indexes_reach_both_update_and_event():
    """Guard: the hoist must not drop either index from its two sinks."""
    m = _run()
    expected_ip = hashlib.sha256(f"ip_address:{_IP}".encode()).hexdigest()
    expected_ua = hashlib.sha256(f"ua:{_UA}".encode()).hexdigest()

    update_params = [p for sql, p in m._db.executes if "last_used_ip_index" in sql][0]
    event_params = [
        p
        for sql, p in m._db.executes
        if "INSERT INTO auth_api_access_token_events" in sql
    ][0]

    assert expected_ip in update_params
    assert expected_ip in event_params
    assert expected_ua in update_params
    assert expected_ua in event_params
