"""Permission changes must refresh every cached view of the user.

The token verification cache holds a ``TokenInfo`` (permission map + account
state flags) for up to ten seconds, and the username lookup cache holds a full
``User`` for even longer.  A grant/revoke that only clears the former leaves a
stale permission map readable through the latter, so a still-valid token keeps
carrying privileges that were already taken away.
"""

from unittest.mock import patch


def _register_and_login(auth_manager, username):
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return user, result


class TestUsernamePermissionCache:
    def test_permission_grant_refreshes_username_cache(self, db, auth_manager):
        user, _ = _register_and_login(auth_manager, "perm_cache_grant_user")

        # Warm the username cache as an ordinary lookup would.
        cached = auth_manager.get_user_by_username("perm_cache_grant_user")
        assert "admin.system" not in cached.permissions

        auth_manager.grant_permission(user.id, "admin.system")

        fresh = auth_manager.get_user_by_username("perm_cache_grant_user")
        assert "admin.system" in fresh.permissions

    def test_permission_revoke_refreshes_username_cache(self, db, auth_manager):
        user, _ = _register_and_login(auth_manager, "perm_cache_revoke_user")
        auth_manager.grant_permission(user.id, "admin.system")
        assert (
            "admin.system"
            in auth_manager.get_user_by_username("perm_cache_revoke_user").permissions
        )

        # update_user replaces the whole permission map, so omitting the
        # previously granted scope revokes it.
        remaining = {
            key: value
            for key, value in user.permissions.items()
            if key != "admin.system"
        }
        auth_manager.update_user(user.id, permissions=remaining)

        fresh = auth_manager.get_user_by_username("perm_cache_revoke_user")
        assert "admin.system" not in fresh.permissions


class TestVerifyPermissionCache:
    def test_permission_grant_refreshes_verify_cache(self, db, auth_manager):
        user, result = _register_and_login(auth_manager, "perm_verify_grant_user")

        # Warm the token verification cache (carries the permission map).
        assert "admin.system" not in auth_manager.verify_token(result.token).permissions

        auth_manager.grant_permission(user.id, "admin.system")

        assert "admin.system" in auth_manager.verify_token(result.token).permissions
