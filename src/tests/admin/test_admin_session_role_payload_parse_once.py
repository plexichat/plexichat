"""Regression: session role payloads are JSON-decoded once per request.

A single privileged admin request can stack several permission gates (e.g. the
migration routes check an action scope *and* ``maintenance``). The role payload
blobs arrive with the validated session and are fixed for the request, but
``check_permission`` used to re-run ``json.loads`` on every gate. The parsed
form is now memoised behind the same request-scoped identity guard as the raw
payloads, so a request with ``g`` gates and ``r`` roles decodes ``r`` blobs,
not ``g * r``. The memo is cleared on re-validation and by privilege mutations.
"""

from unittest.mock import patch

from src.core.admin import permissions as perms
from src.core.admin.request_roles import (
    cached_parsed_session_roles,
    clear_session_roles,
    remember_session_roles,
)

ADMIN_ID = 4242

RAW_PAYLOADS = (
    '{"admin.approvals": true, "users.read": true, "maintenance": true}',
    '{"admin.migrations.execute": true, "admin.audit.read": true}',
    '{"metrics.read": true, "logs.read": true}',
)


class _DB:
    config = {"path": ":memory:"}

    def fetch_all(self, query, params=None):
        raise AssertionError("role query must not run when session roles are cached")

    def fetch_one(self, query, params=None):
        return None


def setup_function():
    clear_session_roles()


def teardown_function():
    clear_session_roles()


def test_payloads_parsed_once_across_stacked_gates():
    db = _DB()
    remember_session_roles(db, ADMIN_ID, RAW_PAYLOADS)

    real_parse = perms._parse_permission_payload
    calls = {"n": 0}

    def counting_parse(raw):
        calls["n"] += 1
        return real_parse(raw)

    with patch.object(perms, "_parse_permission_payload", counting_parse):
        decisions = [
            perms.check_permission(ADMIN_ID, scope, db).has_permission
            for scope in (
                "admin.migrations.execute",
                "maintenance",
                "admin.approvals",
                "users.read",
            )
        ]

    assert all(decisions)
    # Four gates, three roles: the old path issued 12 json.loads; the memo
    # decodes each of the three blobs exactly once.
    assert calls["n"] == len(RAW_PAYLOADS)


def test_parsed_cache_respects_db_and_admin_identity():
    db_a, db_b = _DB(), _DB()
    remember_session_roles(db_a, ADMIN_ID, RAW_PAYLOADS)
    remember_parsed = perms._parse_permission_payload(RAW_PAYLOADS[0])
    from src.core.admin.request_roles import remember_parsed_session_roles

    remember_parsed_session_roles(db_a, ADMIN_ID, (remember_parsed,))

    assert cached_parsed_session_roles(db_a, ADMIN_ID) == (remember_parsed,)
    # A different db or admin id must never observe the cached payloads.
    assert cached_parsed_session_roles(db_b, ADMIN_ID) is None
    assert cached_parsed_session_roles(db_a, ADMIN_ID + 1) is None
    # int/str equivalence for snowflake ids is preserved by the shared guard.
    assert cached_parsed_session_roles(db_a, str(ADMIN_ID)) == (remember_parsed,)


def test_revalidation_and_mutation_clear_parsed_cache():
    db = _DB()
    remember_session_roles(db, ADMIN_ID, RAW_PAYLOADS)
    assert perms.check_permission(ADMIN_ID, "admin.approvals", db).has_permission
    assert cached_parsed_session_roles(db, ADMIN_ID) is not None

    # A fresh session validation drops any parsed form derived from older blobs.
    remember_session_roles(db, ADMIN_ID, RAW_PAYLOADS)
    assert cached_parsed_session_roles(db, ADMIN_ID) is None

    assert perms.check_permission(ADMIN_ID, "admin.approvals", db).has_permission
    assert cached_parsed_session_roles(db, ADMIN_ID) is not None

    # Privilege mutations funnel through this choke point and drop the memo.
    perms.invalidate_admin_permission_caches()
    assert cached_parsed_session_roles(db, ADMIN_ID) is None
