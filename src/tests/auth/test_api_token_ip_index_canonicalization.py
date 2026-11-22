"""API-token usage indexes must key on one canonical spelling of a host.

The per-event ``ip_index`` in ``auth_api_access_token_events`` feeds the
``COUNT(DISTINCT ip_index)`` abuse/usage signal rendered to admins, and
``last_used_ip_index`` records the last source of a token. Both derived from the
raw IP via ``fast_blind_index`` (which only lowercases/strips), so one host
rotating textual spellings in X-Forwarded-For inflated the distinct-IP count
and muddied last-used attribution -- defeating the security signal that a
leaked token is being used from many places.

Non-vacuous: the test first proves the raw spellings hash to different indexes
(the pre-fix behaviour), then proves the canonicalized path collapses them to
one while genuinely distinct hosts stay distinct. Also simulates the pre-fix
code path by disabling the canonicalizer.

Run with::

    pytest src/tests/auth/test_api_token_ip_index_canonicalization.py -q -n 0
"""

import hashlib

import pytest

import src.core.auth.managers.api_token as api_token
from src.core.auth.ip_canonical import canonicalize_ip_address
from src.core.auth.managers.api_token import ApiTokenMixin, _api_token_verify_key

pytestmark = pytest.mark.unit

MAPPED = "::ffff:198.51.100.7"
PLAIN = "198.51.100.7"
V6_SHORT = "2001:db8::1"
V6_EXPANDED = "2001:0DB8:0000:0000:0000:0000:0000:0001"
V6_ZONE = "fe80::1%eth0"
V6_NOZONE = "fe80::1"
OTHER = "198.51.100.8"


class _Crypto:
    def fast_blind_index(self, data, scope):
        return hashlib.sha256(f"{scope}:{data.strip().lower()}".encode()).hexdigest()

    def encrypt_data(self, data, context=None):
        return f"enc:{context}:{data}"


class _DB:
    def __init__(self):
        self.executes = []

    def execute(self, sql, params):
        self.executes.append((sql, tuple(params)))


def _mixin():
    m = object.__new__(ApiTokenMixin)
    m._db = _DB()
    m.crypto = _Crypto()
    m._get_timestamp = lambda: 1_000_000
    m._generate_id = lambda: 4242
    m._ua_index = lambda ua: None
    m._encrypt_ua = lambda ua, ctx: None
    return m


def _event_indexes(m):
    return [p[3] for _sql, p in m._db.executes]


def _record(m, ip):
    m._record_api_access_token_event(
        token_id=1,
        ip_address=ip,
        user_agent=None,
        path="/x",
        method="GET",
        allowed=True,
        scope_match=None,
        reject_reason=None,
    )


# --- non-vacuity ------------------------------------------------------------


def test_raw_blind_indexes_differ_before_canonicalization():
    crypto = _Crypto()
    for a, b in ((MAPPED, PLAIN), (V6_SHORT, V6_EXPANDED), (V6_ZONE, V6_NOZONE)):
        assert crypto.fast_blind_index(a, "ip_address") != crypto.fast_blind_index(
            b, "ip_address"
        )


# --- per-event ip_index / COUNT(DISTINCT ip_index) --------------------------


@pytest.mark.parametrize(
    "spellings,expected",
    [
        ([MAPPED, PLAIN, f"  {PLAIN}  ", MAPPED], PLAIN),
        ([V6_SHORT, V6_EXPANDED], "2001:db8::1"),
        ([V6_ZONE, V6_NOZONE, "fe80::1%1"], "fe80::1"),
        ([f"{MAPPED}%eth0", PLAIN], PLAIN),
    ],
)
def test_event_ip_index_collapses_spellings(spellings, expected):
    m = _mixin()
    for s in spellings:
        _record(m, s)

    want = _Crypto().fast_blind_index(expected, "ip_address")
    assert set(_event_indexes(m)) == {want}
    assert len({canonicalize_ip_address(s) for s in spellings}) == 1


def test_distinct_hosts_keep_distinct_event_indexes():
    m = _mixin()
    for ip in (MAPPED, PLAIN, OTHER, V6_SHORT, "2001:db8::2"):
        _record(m, ip)
    # MAPPED and PLAIN are one host; OTHER, V6_SHORT and the second v6 are not.
    assert len(set(_event_indexes(m))) == 4


def test_prefix_distinct_count_pre_fix_simulation():
    """With the canonicalizer disabled, spelling rotation fakes new IPs."""
    real = api_token.canonicalize_ip_address
    # With the canonicalizer disabled the raw spelling reaches blind_index,
    # which only lowercases/strips. Use spellings that differ even after that
    # normalisation: a whitespace-only variant would collapse with PLAIN.
    api_token.canonicalize_ip_address = lambda v: v.strip() if v else v
    spellings = (MAPPED, PLAIN, f"{MAPPED}%eth0")
    try:
        m = _mixin()
        for s in spellings:
            _record(m, s)
        assert len(set(_event_indexes(m))) == 3  # the inflated count pre-fix
    finally:
        api_token.canonicalize_ip_address = real
    m = _mixin()
    for s in spellings:
        _record(m, s)
    assert len(set(_event_indexes(m))) == 1  # after fix


# --- last_used_ip_index -----------------------------------------------------


def _last_used_index(m):
    for sql, params in m._db.executes:
        if "last_used_ip_index = ?" in sql:
            set_clause = sql.split("SET", 1)[1].split("WHERE", 1)[0]
            # Count placeholders rather than splitting on commas: the
            # COALESCE(use_count_total, 0) assignment contains a comma and
            # would otherwise shift the index by one.
            argno = set_clause[: set_clause.index("last_used_ip_index = ?")].count("?")
            return params[argno]
    raise AssertionError("no last_used_ip_index update captured")


@pytest.mark.parametrize("presented", [MAPPED, PLAIN, V6_EXPANDED, V6_ZONE])
def test_last_used_ip_index_canonicalized(presented):
    m = _mixin()
    token = "a" * 40
    verdict = {
        "authentic": True,
        "token_id": 7,
        "expires_at": None,
        "scope_match": True,
        "has_scopes": False,
        "scope_mode": "none",
        "last_used_at": 0,
        "first_used_at": 1,  # skip the extra first_used_at assignment
    }
    key = _api_token_verify_key(token, presented, None)
    api_token._API_TOKEN_VERIFY_CACHE.set(key, verdict)
    try:
        assert m.verify_api_access_token(token, ip_address=presented) is True
    finally:
        api_token._API_TOKEN_VERIFY_CACHE.clear()
    want = _Crypto().fast_blind_index("198.51.100.7", "ip_address")
    if presented in (V6_EXPANDED,):
        want = _Crypto().fast_blind_index("2001:db8::1", "ip_address")
    if presented in (V6_ZONE,):
        want = _Crypto().fast_blind_index("fe80::1", "ip_address")
    assert _last_used_index(m) == want
