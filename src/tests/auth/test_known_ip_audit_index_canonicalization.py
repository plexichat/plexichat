"""Known-IP and audit-log blind indexes must use one canonical host spelling.

``auth_known_ips`` is ``UNIQUE(user_id, ip_index)`` and ``auth_audit_log``
feeds distinct-IP security queries. Both stored ``ip_index = fast_blind_index(
raw_ip)`` (which only lowercases/strips), so one host rotating textual spellings
in X-Forwarded-For created duplicate known-IP rows and split its audit trail
across indexes -- inflating distinct-IP counts in security telemetry.

Non-vacuous: raw spellings are shown to hash differently (pre-fix), the
canonicalized path collapses them while distinct hosts stay distinct, and a
pre-fix simulation with the canonicalizer disabled reproduces the duplicate
rows.

Run with::

    pytest src/tests/auth/test_known_ip_audit_index_canonicalization.py -q -n 0
"""

import hashlib

import pytest

import src.core.auth.managers.base as base
from src.core.auth.managers.base import AuthManager
from src.core.auth.models import AuditEventType

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
    """Enforces the UNIQUE(user_id, ip_index) constraint on auth_known_ips."""

    def __init__(self):
        self.known = {}
        self.audit = []

    def insert_or_ignore(self, table, columns, values):
        row = dict(zip(columns, values))
        if table == "auth_known_ips":
            self.known.setdefault((row["user_id"], row["ip_index"]), row)
        elif table == "auth_audit_log":
            self.audit.append(row)


def _mixin():
    m = object.__new__(AuthManager)
    m._db = _DB()
    m.crypto = _Crypto()
    m._get_timestamp = lambda: 1_000_000
    counter = iter(range(1, 10_000))

    def _id():
        return next(counter)

    m._generate_id = _id
    return m


def _track(m, ip, user_id=1):
    AuthManager._track_ip(m, user_id, ip)


def _audit(m, ip):
    AuthManager._log_audit(
        m, AuditEventType.LOGIN_SUCCESS, user_id=1, success=True, ip_address=ip
    )


def _want(expected):
    return _Crypto().fast_blind_index(expected, "ip_address")


# --- non-vacuity ------------------------------------------------------------


def test_raw_blind_indexes_differ_before_canonicalization():
    crypto = _Crypto()
    for a, b in ((MAPPED, PLAIN), (V6_SHORT, V6_EXPANDED), (V6_ZONE, V6_NOZONE)):
        assert crypto.fast_blind_index(a, "ip_address") != crypto.fast_blind_index(
            b, "ip_address"
        )


# --- auth_known_ips ---------------------------------------------------------


@pytest.mark.parametrize(
    "spellings,expected",
    [
        ([MAPPED, PLAIN, f"  {PLAIN}  ", MAPPED], PLAIN),
        ([V6_SHORT, V6_EXPANDED], "2001:db8::1"),
        ([V6_ZONE, V6_NOZONE, "fe80::1%1"], "fe80::1"),
        ([f"{MAPPED}%eth0", PLAIN], PLAIN),
    ],
)
def test_known_ip_rows_collapse_spellings(spellings, expected):
    m = _mixin()
    for s in spellings:
        _track(m, s)
    assert list(m._db.known) == [(1, _want(expected))]
    assert len(m._db.known) == 1


def test_known_ips_keep_distinct_hosts_distinct():
    m = _mixin()
    for ip in (MAPPED, PLAIN, OTHER, V6_SHORT, "2001:db8::2"):
        _track(m, ip)
    assert len(m._db.known) == 4  # MAPPED == PLAIN; the rest are distinct


def test_known_ip_pre_fix_simulation_duplicates_rows():
    real = base.canonicalize_ip_address
    base.canonicalize_ip_address = lambda v: v.strip() if v else v
    try:
        # Use spellings that stay different after blind_index's strip/lower;
        # a whitespace-only variant would already collapse with PLAIN.
        m = _mixin()
        for s in (MAPPED, PLAIN, f"{MAPPED}%eth0"):
            _track(m, s)
        assert len(m._db.known) == 3  # duplicate rows pre-fix
    finally:
        base.canonicalize_ip_address = real
    m = _mixin()
    for s in (MAPPED, PLAIN, f"{MAPPED}%eth0"):
        _track(m, s)
    assert len(m._db.known) == 1


# --- auth_audit_log ---------------------------------------------------------


@pytest.mark.parametrize(
    "spellings,expected",
    [
        ([MAPPED, PLAIN, V6_EXPANDED, V6_SHORT, V6_ZONE, V6_NOZONE], None),
    ],
)
def test_audit_indexes_collapse_spellings(spellings, expected):
    m = _mixin()
    for s in spellings:
        _audit(m, s)
    indexes = [r["ip_index"] for r in m._db.audit]
    from src.core.auth.ip_canonical import canonicalize_ip_address

    assert len(set(canonicalize_ip_address(s) for s in spellings)) == 3
    assert len(set(indexes)) == 3


def test_audit_indexes_keep_distinct_hosts_distinct():
    m = _mixin()
    for ip in (MAPPED, PLAIN, OTHER, V6_SHORT, "2001:db8::2"):
        _audit(m, ip)
    assert len({r["ip_index"] for r in m._db.audit}) == 4


def test_audit_pre_fix_simulation_inflates_distinct_count():
    real = base.canonicalize_ip_address
    base.canonicalize_ip_address = lambda v: v.strip() if v else v
    try:
        m = _mixin()
        for s in (MAPPED, PLAIN, f"{MAPPED}%eth0"):
            _audit(m, s)
        assert len({r["ip_index"] for r in m._db.audit}) == 3
    finally:
        base.canonicalize_ip_address = real
    m = _mixin()
    for s in (MAPPED, PLAIN, f"{MAPPED}%eth0"):
        _audit(m, s)
    assert len({r["ip_index"] for r in m._db.audit}) == 1
