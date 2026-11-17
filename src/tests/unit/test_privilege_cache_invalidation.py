"""Privilege mutations must drop the cached auth context.

Role grants/revocations and direct account-lock writes bypass the normal
``auth.update_user`` path, so they need an explicit invalidation.  Otherwise a
bearer or CSRF-protected cookie token keeps answering with the pre-change
permission map until the verify cache TTL expires.
"""

import asyncio
import json
from unittest.mock import patch

from starlette.requests import Request

from src.core.auth.privilege_cache import invalidate_privilege_caches


def _request(method="POST", path="/roles/assign") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


class FakeDB:
    def __init__(self, one=None):
        self.one = one
        self.executed = []

    def fetch_one(self, query, params=None):
        return self.one

    def fetch_all(self, query, params=None):
        return []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return None


def _hierarchy(**overrides):
    base = {
        "current_pos": 50,
        "target_pos": 10,
        "admin_exists": True,
        "role_pos": 5,
        "role_is_system": False,
        "role_permissions": json.dumps({"users.read": True}),
        "target_exists": True,
        "account_locked": 0,
    }
    base.update(overrides)
    return base


class TestInvalidatePrivilegeCaches:
    def test_helper_clears_every_auth_cache_prefix(self):
        with patch("src.core.database.invalidate_pattern") as invalidate:
            invalidate_privilege_caches()
        invalidated = {call.args[0] for call in invalidate.call_args_list}
        assert invalidated == {
            "token_verify:*",
            "user_data:*",
            "user_by_username:*",
            "user_profile:*",
        }


class TestRoleMutationInvalidation:
    def test_update_role_invalidates_cache(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleUpdate

        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "_require_role_manager", return_value=1),
            patch.object(role_routes, "_load_hierarchy", return_value=_hierarchy()),
            patch("src.api.get_db", return_value=FakeDB()),
            patch.object(role_routes, "invalidate_privilege_caches") as invalidate,
            patch("src.core.admin.permissions.log_admin_action"),
        ):
            asyncio.run(
                role_routes.update_role(_request(), 7, RoleUpdate(description="x"))
            )
        invalidate.assert_called_once_with()

    def test_assign_role_invalidates_cache(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleAssignment

        body = RoleAssignment(admin_id="2", role_id="7")
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "_require_role_manager", return_value=1),
            patch.object(role_routes, "_load_hierarchy", return_value=_hierarchy()),
            patch("src.api.get_db", return_value=FakeDB(one=None)),
            patch(
                "src.core.admin.permissions.can_delegate_permissions",
                return_value=True,
            ),
            patch.object(role_routes, "invalidate_privilege_caches") as invalidate,
            patch("src.core.admin.permissions.log_admin_action"),
        ):
            asyncio.run(role_routes.assign_role(_request(), body))
        invalidate.assert_called_once_with()

    def test_revoke_role_invalidates_cache(self):
        from src.api.routes.admin import roles as role_routes

        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "_require_role_manager", return_value=1),
            patch.object(role_routes, "_load_hierarchy", return_value=_hierarchy()),
            patch("src.api.get_db", return_value=FakeDB(one={"admin_id": 2})),
            patch.object(role_routes, "invalidate_privilege_caches") as invalidate,
            patch("src.core.admin.permissions.log_admin_action"),
        ):
            asyncio.run(role_routes.revoke_role(_request(), 2, 7))
        invalidate.assert_called_once_with()

    def test_delete_role_invalidates_cache(self):
        from src.api.routes.admin import roles as role_routes

        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "_require_role_manager", return_value=1),
            patch.object(role_routes, "_load_hierarchy", return_value=_hierarchy()),
            patch("src.api.get_db", return_value=FakeDB(one={"count": 0})),
            patch.object(role_routes, "invalidate_privilege_caches") as invalidate,
            patch("src.core.admin.permissions.log_admin_action"),
        ):
            asyncio.run(role_routes.delete_role(_request(), 7))
        invalidate.assert_called_once_with()


class TestAccountStatusInvalidation:
    def test_toggle_admin_status_invalidates_cache(self):
        from src.api.routes.admin.users import admin_users as users_module

        db = FakeDB()
        stub = type(
            "Stub",
            (),
            {
                "_get_db": lambda self: db,
                "_parse_user_id": lambda self, value: int(value),
                "toggle_admin_user_status": (
                    users_module.AdminUsersMixin.toggle_admin_user_status
                ),
            },
        )()
        with (
            patch.object(users_module, "check_host_restriction"),
            patch.object(users_module, "require_admin_permission", return_value=1),
            patch.object(
                users_module,
                "_load_admin_hierarchy",
                return_value=_hierarchy(),
            ),
            patch.object(users_module, "invalidate_privilege_caches") as invalidate,
            patch("src.core.admin.permissions.log_admin_action"),
        ):
            asyncio.run(stub.toggle_admin_user_status(_request(), "2"))
        invalidate.assert_called_once_with()
