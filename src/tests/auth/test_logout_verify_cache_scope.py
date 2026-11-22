"""A single-user logout must not flush every other user's verify verdicts.

``AuthManager.logout(token)`` revokes exactly one session -- the credential it
is handed -- but drops the whole shared ``token_verify:*`` keyspace. Each
verdict is memoised under a key derived from *that credential's* hash (plus an
IP/User-Agent binding suffix), so one ordinary user logging out currently clears
the cached ``TokenInfo`` of every other authenticated user. The next request
from each of them pays for a full DB re-verification: a thundering herd driven
by any low-privileged account's logout.

This test warms several unrelated users' verdicts, logs one of them out, and
counts the surviving keys. Two properties must hold together:

* security -- every cached verdict for the revoked credential is gone, so it
  cannot keep authenticating for the rest of the verify TTL; and
* availability -- unrelated users' verdicts survive the single-user action.

A fix that simply stops invalidating anything fails the first assertion; the
current broad flush fails the second.
"""

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.privilege_cache import invalidate_privilege_caches
from src.core.auth.tokens import safe_token_cache_key

pytestmark = pytest.mark.auth

_IP = "198.51.100.41"
_OTHER_IP = "198.51.100.42"
_UA = "logout-verify-scope/1.0"


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
        return auth_manager.login(username, "TestPass123!").token


def _warm_keys():
    from src.core.database.cache.base import mem_cache_matching_keys

    return set(mem_cache_matching_keys("token_verify:str:*"))


def test_logout_invalidates_only_the_logged_out_token(
    db, auth_manager, test_user_with_token
):
    mgr = _manager()
    target = test_user_with_token["token"]
    target_uid = int(test_user_with_token["user"].id)

    # Three unrelated credentials, each a distinct user's session.
    others = []
    for i in range(3):
        name = f"logout_scope_other_{i}"
        auth_manager.register(
            username=name,
            email=f"{name}@example.com",
            password="TestPass123!",
        )
        others.append((name, _login(auth_manager, name)))

    # Warm the target in two binding contexts: one token may own several cache
    # entries, and revocation must drop every variant, not just one.
    assert (
        mgr.verify_token(target, ip_address=_IP, user_agent=_UA).user_id == target_uid
    )
    assert (
        mgr.verify_token(target, ip_address=_OTHER_IP, user_agent=_UA).user_id
        == target_uid
    )

    other_uids = []
    for _name, token in others:
        info = mgr.verify_token(token, ip_address=_IP, user_agent=_UA)
        other_uids.append((token, info.user_id))

    before = _warm_keys()
    target_prefix = safe_token_cache_key(target)
    target_keys = {k for k in before if k.startswith(target_prefix)}
    other_keys = before - target_keys
    assert len(target_keys) >= 2, "the target must own >1 warm verdict variant"
    assert len(other_keys) >= len(other_uids), "unrelated users must be warm too"

    # The normal-user action under test.
    assert mgr.logout(target) is True

    after = _warm_keys()
    # Security: every binding variant of the revoked credential is gone.
    assert not {k for k in after if k.startswith(target_prefix)}, (
        "logout left a cached verdict for the revoked credential"
    )
    # Availability: unrelated users' verdicts survive one user's logout.
    assert other_keys <= after, (
        "one user's logout flushed "
        f"{len(other_keys - after)} unrelated users' verify keys"
    )

    # Fail-closed: the revoked token cannot ride a stale cached verdict.
    with pytest.raises(TokenInvalidError):
        mgr.verify_token(target, ip_address=_IP, user_agent=_UA)

    # Unrelated credentials are still accepted from their surviving cache.
    for token, uid in other_uids:
        assert mgr.verify_token(token, ip_address=_IP, user_agent=_UA).user_id == uid
