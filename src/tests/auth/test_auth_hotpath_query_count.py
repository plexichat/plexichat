"""Query-count guards for the hot auth endpoints.

The self-test times login/register/2FA/DSAR end to end. Argon2 verification is
intentionally expensive and is never weakened here; these guards instead lock
in the surrounding data-access shape so a future change cannot reintroduce a
redundant user fetch on those hot paths. Budgets are expressed as query counts
and call assertions, not wall-clock time, so they stay stable on a loaded
runner.
"""

import importlib
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def dsar_mod():
    return importlib.import_module("src.api.routes.users.dsar")


def test_dsar_verify_password_uses_one_lookup(dsar_mod, db, auth_manager, monkeypatch):
    """DSAR password re-confirm must not fetch the user a second time."""
    user = auth_manager.register(
        "dsar_query_guard", "dsar_query_guard@example.com", "TestPass123!"
    )

    fake_auth = MagicMock()
    # The old implementation called auth.get_user() only for existence, then
    # issued a separate hash SELECT. That full user fetch must be gone.
    fake_auth.get_user.side_effect = AssertionError(
        "DSAR password verify must not call auth.get_user()"
    )
    monkeypatch.setattr(dsar_mod.api, "get_auth", lambda: fake_auth)
    monkeypatch.setattr(dsar_mod.api, "get_db", lambda: db)

    mixin = dsar_mod.DataExportMixin()

    # fetch_one() has a short-lived result cache; clear it so the count
    # reflects real statements (same pattern as the database perf guards).
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()
    assert mixin._verify_password(user.id, "TestPass123!") is True
    assert db.get_request_metrics()["query_count"] == 1, (
        "query-count regression: DSAR password verify must be a single SELECT "
        f"(got {db.get_request_metrics()['query_count']})"
    )
    fake_auth.get_user.assert_not_called()

    # A wrong password still costs exactly one lookup (Argon2 dominates, the
    # DB access must not grow).
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()
    assert mixin._verify_password(user.id, "wrong-password") is False
    assert db.get_request_metrics()["query_count"] == 1


def test_create_session_under_cap_uses_count_only(db, auth_manager, monkeypatch):
    """Session creation must not decrypt every active session to get a count."""
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    user = auth_manager.register(
        "session_cap_guard", "session_cap_guard@example.com", "TestPass123!"
    )

    real_get_sessions = mgr.get_sessions
    calls = {"full_fetches": 0}

    def spy(user_id):
        calls["full_fetches"] += 1
        return real_get_sessions(user_id)

    monkeypatch.setattr(mgr, "get_sessions", spy)

    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()
    session = mgr._create_session(user.id, None, None, None)

    assert session is not None
    # Under the default cap (10) with no active sessions, the full/decrypting
    # fetch must be skipped entirely.
    assert calls["full_fetches"] == 0
    # One COUNT(*) plus the session INSERT: no per-session fetch/decrypt.
    assert db.get_request_metrics()["query_count"] == 2, (
        "query-count regression: under-cap session creation should be a COUNT "
        f"and an INSERT, got {db.get_request_metrics()['query_count']}"
    )


def test_create_session_over_cap_still_evicts_oldest(db, auth_manager, monkeypatch):
    """The count-first fast path must preserve eviction semantics at the cap."""
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    user = auth_manager.register(
        "session_evict_guard", "session_evict_guard@example.com", "TestPass123!"
    )
    monkeypatch.setattr(
        mgr,
        "_config",
        {
            **mgr._config,
            "sessions": {**mgr._config.get("sessions", {}), "max_per_user": 1},
        },
    )

    first = mgr._create_session(user.id, None, None, None)
    second = mgr._create_session(user.id, None, None, None)

    active_ids = {s.id for s in mgr.get_sessions(user.id)}
    assert active_ids == {second.id}
    assert first.id not in active_ids
