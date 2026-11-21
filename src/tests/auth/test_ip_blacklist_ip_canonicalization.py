"""The IP blacklist must key on one canonical spelling of a host.

Follow-up to the IPv6 zone-id fix (91ab5154), which canonicalized the rate-limit
principal and the login failure budget but left the IP blacklist deriving its
``ip_index`` blind index (and its ``ip_blocked:*`` cache key) from the raw IP
string. ``fast_blind_index`` only lowercases/strips, so two textual spellings of
one host produced two different rows. An admin blocking ``::ffff:198.51.100.7``
did not block a client the resolver surfaced as ``198.51.100.7``, and a banned
client could evade the ban by respelling its address in X-Forwarded-For.

These tests are non-vacuous: they first show the raw spellings hash to
*different* indexes (the pre-fix behaviour), then show the canonicalized path
makes a block/unblock apply across every spelling while distinct hosts remain
distinct.

Run with::

    pytest src/tests/auth/test_ip_blacklist_ip_canonicalization.py -q -n 0
"""

import hashlib
from unittest.mock import patch

import pytest

from src.core.auth.ip_canonical import canonicalize_ip_address
from src.core.auth.managers.ip_blacklist import (
    IpBlacklistMixin,
    _ip_blocked_cache_key,
)

pytestmark = pytest.mark.unit


# Same host, different spellings.
MAPPED_V4 = "::ffff:198.51.100.7"
PLAIN_V4 = "198.51.100.7"
V6_SHORT = "2001:db8::1"
V6_EXPANDED = "2001:0DB8:0000:0000:0000:0000:0000:0001"
V6_ZONE = "fe80::1%eth0"
V6_NOZONE = "fe80::1"

# A genuinely different host.
OTHER_HOST = "198.51.100.8"


class _FakeCrypto:
    """Deterministic stand-in that mirrors blind_index's lower/strip hashing."""

    def fast_blind_index(self, data, scope):
        return hashlib.sha256(f"{scope}:{data.strip().lower()}".encode()).hexdigest()

    def legacy_fast_blind_index(self, data, scope):
        return ""

    def encrypt_data(self, data, context=None):
        return f"enc:{context}:{data}"


class _FakeDB:
    def __init__(self):
        self.rows = {}

    def upsert(self, table, columns, values, conflict_columns=None):
        self.rows[values[0]] = dict(zip(columns, values))
        return True

    def execute(self, sql, params):
        idx, legacy, _ = params
        for key in list(self.rows):
            if key == idx or (key == legacy and legacy != ""):
                del self.rows[key]
        return True

    def fetch_one(self, sql, params):
        idx, legacy, _ = params
        for key in (idx, legacy):
            if key and key in self.rows:
                return dict(self.rows[key])
        return None


def _mixin():
    mixin = object.__new__(IpBlacklistMixin)
    mixin._db = _FakeDB()
    mixin.crypto = _FakeCrypto()
    mixin._get_timestamp = lambda: 1_000_000
    return mixin


def _blocked(mixin, ip):
    # Bypass the @cached tier; the DB/blind-index path is what matters here.
    return IpBlacklistMixin.is_ip_blocked.__wrapped__(mixin, ip)


# --- 1. the canonicalizer itself -------------------------------------------


@pytest.mark.parametrize(
    "spellings,expected",
    [
        ([MAPPED_V4, PLAIN_V4, f"  {PLAIN_V4}  "], PLAIN_V4),
        ([V6_SHORT, V6_EXPANDED], "2001:db8::1"),
        ([V6_ZONE, V6_NOZONE, "fe80::1%1"], "fe80::1"),
        ([f"{MAPPED_V4}%eth0", PLAIN_V4], PLAIN_V4),
    ],
)
def test_canonicalizer_collapses_spellings(spellings, expected):
    assert {canonicalize_ip_address(s) for s in spellings} == {expected}


def test_canonicalizer_keeps_distinct_hosts_distinct():
    assert canonicalize_ip_address(PLAIN_V4) != canonicalize_ip_address(OTHER_HOST)
    assert canonicalize_ip_address(V6_SHORT) != canonicalize_ip_address("2001:db8::2")


# --- 2. non-vacuity: raw blind indexes really differ -----------------------


def test_raw_blind_indexes_differ_before_canonicalization():
    """Control: the spellings are NOT already equal to blind_index."""
    crypto = _FakeCrypto()
    for pair in ((MAPPED_V4, PLAIN_V4), (V6_SHORT, V6_EXPANDED), (V6_ZONE, V6_NOZONE)):
        raw_a = crypto.fast_blind_index(pair[0], "ip_address")
        raw_b = crypto.fast_blind_index(pair[1], "ip_address")
        assert raw_a != raw_b  # pre-fix these indexed different rows


# --- 3. ban applies across every spelling of the blocked host -------------


@pytest.mark.parametrize(
    "block_as,check_as",
    [
        (MAPPED_V4, PLAIN_V4),
        (PLAIN_V4, MAPPED_V4),
        (V6_SHORT, V6_EXPANDED),
        (V6_EXPANDED, V6_SHORT),
        (f"  {PLAIN_V4}  ", MAPPED_V4),
    ],
)
def test_block_applies_across_spellings(block_as, check_as):
    mixin = _mixin()
    with patch("src.core.auth.managers.ip_blacklist.invalidate_pattern"):
        mixin.block_ip(block_as)
    assert _blocked(mixin, check_as) is True


@pytest.mark.parametrize(
    "block_as,check_as",
    [
        (V6_ZONE, V6_NOZONE),
        (V6_NOZONE, V6_ZONE),
        (f"{MAPPED_V4}%eth0", PLAIN_V4),
    ],
)
def test_zone_and_mapped_spellings_share_a_ban(block_as, check_as):
    mixin = _mixin()
    with patch("src.core.auth.managers.ip_blacklist.invalidate_pattern"):
        mixin.block_ip(block_as)
    assert _blocked(mixin, check_as) is True


def test_ban_does_not_spread_to_a_distinct_host():
    mixin = _mixin()
    with patch("src.core.auth.managers.ip_blacklist.invalidate_pattern"):
        mixin.block_ip(PLAIN_V4)
    assert _blocked(mixin, PLAIN_V4) is True
    assert _blocked(mixin, OTHER_HOST) is False
    assert _blocked(mixin, "2001:db8::2") is False


def test_unblock_across_spellings():
    mixin = _mixin()
    with patch("src.core.auth.managers.ip_blacklist.invalidate_pattern") as inv:
        mixin.block_ip(MAPPED_V4)
        assert _blocked(mixin, PLAIN_V4) is True
        mixin.unblock_ip(PLAIN_V4)
    assert _blocked(mixin, MAPPED_V4) is False
    assert inv.called


# --- 4. the ip_blocked:* cache key is canonical too ------------------------


def test_cache_key_collapses_spellings():
    assert {_ip_blocked_cache_key(None, s) for s in (MAPPED_V4, PLAIN_V4)} == {
        f"ip_blocked:{PLAIN_V4}"
    }
    assert {_ip_blocked_cache_key(None, s) for s in (V6_ZONE, V6_NOZONE)} == {
        f"ip_blocked:{V6_NOZONE}"
    }
    # keyword form (used when callers pass ip_address=...)
    assert _ip_blocked_cache_key(None, ip_address=V6_EXPANDED) == _ip_blocked_cache_key(
        None, ip_address=V6_SHORT
    )
