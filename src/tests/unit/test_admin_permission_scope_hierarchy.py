"""Scope hierarchy: a caller may only delegate privileges it already holds.

The position hierarchy alone is not enough -- without this check a
lower-position admin could mint a role containing scopes above its own and
then hand it to itself or a peer.  ``can_delegate_permissions`` closes that
gap and both role write routes enforce it.
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.schemas.common import SnowflakeIDParam
from src.core.admin.permissions import can_delegate_permissions


def _request(path: str = "/roles", method: str = "POST") -> Request:
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


class TestCanDelegatePermissions:
    @pytest.mark.parametrize(
        ("held", "requested", "expected"),
        [
            (frozenset({"users.read"}), {"users.read": True}, True),
            (frozenset({"users.read"}), {"users.edit": False}, True),
            (frozenset({"users.read"}), {"config.security": True}, False),
            (frozenset({"users.*"}), {"users.delete": True}, True),
            (frozenset({"users.*"}), {"config.security": True}, False),
            (frozenset({"*"}), {"config.security": True}, True),
            (frozenset(), {"users.read": True}, False),
        ],
    )
    def test_delegation_follows_held_scopes(self, held, requested, expected):
        with patch(
            "src.core.admin.permissions.get_admin_granted_permissions",
            return_value=held,
        ):
            assert can_delegate_permissions(object(), 1, requested) is expected


class TestRoleRoutesEnforceScopeHierarchy:
    def test_create_role_rejects_unheld_permission(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleCreate

        body = RoleCreate(
            name="escalation",
            description="bad",
            permissions={"config.security": True},
            position=0,
        )
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "require_admin_permission", return_value=1),
            patch(
                "src.core.admin.permissions.get_admin_granted_permissions",
                return_value=frozenset({"users.read"}),
            ),
            patch("src.api.get_db"),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(role_routes.create_role(_request(), body))
        assert exc.value.status_code == 403

    def test_update_role_rejects_unheld_permission(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleUpdate

        hierarchy = {
            "current_pos": 50,
            "target_pos": 50,
            "admin_exists": True,
            "role_pos": 10,
            "role_is_system": False,
        }
        body = RoleUpdate(description="test", permissions={"admin.roles": True})
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "require_admin_permission", return_value=1),
            patch.object(role_routes, "_load_hierarchy", return_value=hierarchy),
            patch(
                "src.core.admin.permissions.get_admin_granted_permissions",
                return_value=frozenset({"users.read"}),
            ),
            patch("src.api.get_db"),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(role_routes.update_role(_request("PUT"), SnowflakeIDParam(1), body))
        assert exc.value.status_code == 403
