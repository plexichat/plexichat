"""Object-level authorization for concrete admin-role objects.

Route-level gates and position hierarchy are necessary but not sufficient:
assigning an existing low-position role that carries scopes the actor does not
hold would launder privileges.  The object-level check rejects that.
"""

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.schemas.common import SnowflakeID
from src.core.admin.permissions import can_grant_existing_role


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/roles/assign",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


def _db_with_role_permissions(raw):
    class FakeDB:
        def fetch_one(self, query, params=None):
            return {"permissions": raw}

    return FakeDB()


class TestCanGrantExistingRole:
    def test_role_with_unheld_permission_is_denied(self):
        db = _db_with_role_permissions(json.dumps({"config.security": True}))
        with patch(
            "src.core.admin.permissions.get_admin_granted_permissions",
            return_value=frozenset({"users.read"}),
        ):
            assert can_grant_existing_role(db, 1, 7) is False

    def test_super_admin_can_grant_any_role(self):
        db = _db_with_role_permissions(json.dumps({"config.security": True}))
        with patch(
            "src.core.admin.permissions.get_admin_granted_permissions",
            return_value=frozenset({"*"}),
        ):
            assert can_grant_existing_role(db, 1, 7) is True

    @pytest.mark.parametrize("raw", [None, 123, "not json", ["users.read"]])
    def test_malformed_role_row_fails_closed(self, raw):
        with patch(
            "src.core.admin.permissions.get_admin_granted_permissions",
            return_value=frozenset({"*"}),
        ):
            assert can_grant_existing_role(_db_with_role_permissions(raw), 1, 7) is False

    def test_missing_role_fails_closed(self):
        class EmptyDB:
            def fetch_one(self, query, params=None):
                return None

        assert can_grant_existing_role(EmptyDB(), 1, 7) is False


class TestAssignRoleObjectGate:
    def test_assign_denies_role_carrying_unheld_permissions(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleAssignment

        hierarchy = {
            "current_pos": 50,
            "target_pos": 10,
            "admin_exists": True,
            "role_pos": 5,
            "role_is_system": False,
            "role_permissions": json.dumps({"config.security": True}),
        }
        body = RoleAssignment(admin_id=SnowflakeID("2"), role_id=SnowflakeID("7"))
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "require_admin_permission", return_value=1),
            patch.object(role_routes, "_load_hierarchy", return_value=hierarchy),
            patch(
                "src.core.admin.permissions.can_grant_existing_role",
                return_value=False,
            ),
            patch(
                "src.core.admin.permissions.get_admin_granted_permissions",
                return_value=frozenset({"users.read"}),
            ),
            patch("src.api.get_db"),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(role_routes.assign_role(_request(), body))
        assert exc.value.status_code == 403

    def test_assign_route_source_declares_object_check(self):
        import inspect

        from src.api.routes.admin import roles as role_routes

        source = inspect.getsource(role_routes.assign_role)
        # The check may resolve the role payload through the dedicated helper
        # or through the batched hierarchy lookup; both enforce the same rule.
        assert (
            "can_grant_existing_role(db, admin_id, target_role_id)" in source
            or "can_delegate_permissions(" in source
        )
