"""Mass revocation must not leave a warm session-token verify verdict.

``AuthManager.verify_token`` memoises each session's ``TokenInfo`` (user id,
permission map, account flags) under ``token_verify:*`` for up to ten seconds.
Every sibling revocation path (``logout``, ``revoke_session``,
``revoke_sessions_bulk``, ``revoke_device``, ...) invalidates that keyspace
*after* its write, with an explicit comment that doing it before would let a
concurrent verification re-cache the still-valid row.

``logout_all`` / ``logout_all_users`` did the opposite: they invalidated at
the very top of the method, then revoked the rows in a later statement. A
verification landing in that window re-warms the verdict from the still-live
session, so the mass revocation completes but the credential keeps
authenticating from cache for the rest of the verify TTL. This is the same
stale-revocation class the privilege-cache work closed, on a different cache
and a different set of revocation entry points.

The regression simulates that interleaving deterministically: it re-verifies
the token at the instant the revocation UPDATE executes and asserts that the
token is rejected once ``logout_all`` returns. A legitimate unrelated token
is used as a control so a fix that simply flushes too much cannot pass.
"""

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.privilege_cache import invalidate_privilege_caches

pytestmark = pytest.mark.auth

_IP = "198.51.100.23"
_UA = "logout-all-race-regression/1.0"


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    invalidate_privilege_caches()
    yield
    invalidate_privilege_caches()


def _manager():
    from src.core.auth._lazy import _get_auth_manager

    return _get_auth_manager().get_instance()


def _login(auth_manager, username: str) -> str:
    from unittest.mock import patch
    from src.utils import encryption

    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return result.token


def _install_interleaving(monkeypatch, db, mgr, token):
    """Re-verify ``token`` at the instant a revocation UPDATE runs.

    Models a concurrent request that lands between the two operations in the
    buggy implementation. The ``in_verify`` guard keeps the nested
    ``last_activity`` UPDATE of that verification from recursing.
    """
    original_execute = db.execute
    state = {"in_verify": False, "recached": False}

    def execute_with_concurrent_verify(sql, params=()):
        if (
            not state["in_verify"]
            and not state["recached"]
            and isinstance(sql, str)
            and "UPDATE auth_sessions SET revoked = 1" in sql
        ):
            state["recached"] = True
            state["in_verify"] = True
            try:
                info = mgr.verify_token(token, ip_address=_IP, user_agent=_UA)
                assert info.valid, "the pre-revocation verdict must be live"
            finally:
                state["in_verify"] = False
        return original_execute(sql, params)

    monkeypatch.setattr(db, "execute", execute_with_concurrent_verify)
    return state


def test_logout_all_rejects_verdict_recached_during_revocation(
    db, auth_manager, test_user_with_token, monkeypatch
):
    mgr = _manager()
    token = test_user_with_token["token"]
    uid = int(test_user_with_token["user"].id)

    # Control credential: a different user whose session must survive.
    control = auth_manager.register(
        username="logout_all_race_control",
        email="logout_all_race_control@example.com",
        password="TestPass123!",
    )
    control_token = _login(auth_manager, "logout_all_race_control")

    # Warm the verdict while the target session is live.
    info = mgr.verify_token(token, ip_address=_IP, user_agent=_UA)
    assert info.valid and info.user_id == uid
    assert mgr.verify_token(
        control_token, ip_address=_IP, user_agent=_UA
    ).user_id == int(control.id)

    state = _install_interleaving(monkeypatch, db, mgr, token)
    assert mgr.logout_all(uid) >= 1
    assert state["recached"], "the test must actually exercise the race window"

    # The revoked credential must fail closed despite being re-warmed mid-write.
    with pytest.raises(TokenInvalidError):
        mgr.verify_token(token, ip_address=_IP, user_agent=_UA)

    # The unrelated, never-revoked token is still good.
    assert mgr.verify_token(
        control_token, ip_address=_IP, user_agent=_UA
    ).user_id == int(control.id)


def test_logout_all_users_rejects_verdict_recached_during_revocation(
    db, auth_manager, test_user_with_token, monkeypatch
):
    mgr = _manager()
    token = test_user_with_token["token"]

    info = mgr.verify_token(token, ip_address=_IP, user_agent=_UA)
    assert info.valid

    state = _install_interleaving(monkeypatch, db, mgr, token)
    assert mgr.logout_all_users() == 1
    assert state["recached"]

    with pytest.raises(TokenInvalidError):
        mgr.verify_token(token, ip_address=_IP, user_agent=_UA)
