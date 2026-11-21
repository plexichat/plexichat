"""Regression: purging an account must drop its warm session-token verify verdict.

``AuthManager.verify_token`` memoises each session's ``TokenInfo`` (user id,
permissions, account flags) in the bounded ``token_verify:*`` cache for up to
ten seconds. ``logout_all`` and every privilege/role mutation drop that
keyspace, but ``AccountReaper.purge_user`` -- used by reaper harvest, admin
force-purge and the self-test cleanup -- deleted the ``auth_sessions`` and
``auth_users`` rows without ever invalidating it. A bearer/cookie token used
just before the purge therefore kept authenticating (with the deleted user's
full privileges) from the warm verdict until natural cache eviction, even
though the account and its session were already gone.

The purge now invalidates the auth privilege caches alongside the session and
account deletions, so every purge path revokes the credential immediately.
"""

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.privilege_cache import invalidate_privilege_caches

pytestmark = pytest.mark.auth

_IP = "203.0.113.9"
_UA = "reaper-purge-session-regression/1.0"


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    invalidate_privilege_caches()
    yield
    invalidate_privilege_caches()


def test_purge_drops_warm_session_token_verify_verdict(
    db, auth_manager, test_user_with_token, tmp_path, monkeypatch
):
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    user = test_user_with_token["user"]
    token = test_user_with_token["token"]

    # Count fresh session lookups: the @cached decorator only calls
    # _verify_session_token on a miss, so a warm verdict leaves this untouched.
    lookups = []
    original = mgr._verify_session_token

    def counting(parsed, ip_address=None, user_agent=None):
        lookups.append(parsed["id"])
        return original(parsed, ip_address, user_agent)

    monkeypatch.setattr(mgr, "_verify_session_token", counting)

    # Warm the in-process verdict while the account and session are live.
    info = auth_manager.verify_token(token, ip_address=_IP, user_agent=_UA)
    assert info.valid and info.user_id == int(user.id)
    assert len(lookups) == 1

    # A repeat within the TTL is served from the cache (no new DB lookup).
    auth_manager.verify_token(token, ip_address=_IP, user_agent=_UA)
    assert len(lookups) == 1, "the verify verdict should be cached"

    assert auth_manager.force_purge_account(int(user.id), admin_id=999) is True

    # The account and its sessions are gone with the purge ...
    assert (
        db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (int(user.id),)) is None
    )
    assert (
        db.fetch_one("SELECT id FROM auth_sessions WHERE user_id = ?", (int(user.id),))
        is None
    )

    # ... so the warm verdict must not keep the purged token authenticating.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(token, ip_address=_IP, user_agent=_UA)
    assert len(lookups) == 2, (
        "the purge must invalidate the cached verify verdict so the next call "
        "hits the database and fails closed"
    )
