"""Hot-path guard: trusted-device probes must not re-introspect the schema.

``is_trusted_device`` runs on every login where 2FA is enabled and a device
fingerprint is present.  The schema-verdict helper it calls used to issue a
``PRAGMA table_info(auth_devices)`` on *every* probe.  Writes on the login path
clear the short-lived result cache, so that introspection really re-executes
per request.  Once the trusted columns exist they are permanent (ADD COLUMN),
so the verdict can be memoized; this test counts the resulting queries.
"""

import pytest

pytestmark = pytest.mark.unit


def _clear_query_cache(db):
    with db._query_cache_lock:
        db._query_cache.clear()


def test_is_trusted_device_is_one_query_after_schema_warmup(db, auth_manager):
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    user = auth_manager.register(
        "trusted_probe_guard", "trusted_probe_guard@example.com", "TestPass123!"
    )
    fp = "fingerprint-trusted-probe-0001"
    mgr._track_device(user.id, {"fingerprint": fp})

    # First call establishes the schema verdict (and pays for introspection).
    assert mgr.is_trusted_device(user.id, fp) is False
    assert mgr.trust_device(user.id, fp) is True
    assert mgr.is_trusted_device(user.id, fp) is True

    # Simulate the login path: a write flushed the short-lived result cache,
    # so the next probe must still avoid re-running PRAGMA introspection.
    _clear_query_cache(db)
    db.reset_request_metrics()
    assert mgr.is_trusted_device(user.id, fp) is True
    count = db.get_request_metrics()["query_count"]
    assert count == 1, (
        "trusted-device probe re-ran schema introspection: expected a single "
        f"SELECT on the trusted column, got {count} queries"
    )
