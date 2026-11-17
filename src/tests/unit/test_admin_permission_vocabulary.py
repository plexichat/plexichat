"""Closed permission vocabulary for delegated admin roles.

Wildcard scopes (``*`` and ``scope.*``) and unknown/unconventional keys must
never be accepted from a role create/update payload.  The vocabulary lives in
``src.core.admin.permissions`` and both mutating routes funnel through it.
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.schemas.common import SnowflakeIDParam
from src.core.admin.permissions import (
    DELEGABLE_PERMISSIONS,
    PERMISSION_SCOPES,
    validate_delegable_permissions,
)


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


class TestDelegableVocabulary:
    def test_wildcards_are_excluded_from_delegable_set(self):
        assert "*" not in DELEGABLE_PERMISSIONS
        assert not any(key.endswith(".*") for key in DELEGABLE_PERMISSIONS)

    def test_vocabulary_is_derived_from_declared_scopes(self):
        assert DELEGABLE_PERMISSIONS == frozenset(
            key
            for key in PERMISSION_SCOPES
            if key != "*" and not key.endswith(".*")
        )
        assert "users.read" in DELEGABLE_PERMISSIONS
        assert "admin.roles" in DELEGABLE_PERMISSIONS

    @pytest.mark.parametrize(
        "payload",
        [
            {"*": True},
            {"admin.*": True},
            {"users.*": True},
            {"totally.made.up": True},
            {"users.read": "yes"},
            {"users.read": 1},
            {1: True},
            "users.read",
            None,
        ],
    )
    def test_invalid_maps_are_rejected(self, payload):
        with pytest.raises(ValueError):
            validate_delegable_permissions(payload)

    def test_valid_leaf_map_is_accepted_and_copied(self):
        payload = {"users.read": True, "users.delete": False}
        assert validate_delegable_permissions(payload) == payload


class TestRoleRoutesUseClosedVocabulary:
    def test_create_role_rejects_unknown_scope(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleCreate

        body = RoleCreate(
            name="bad-vocab",
            description="bad",
            permissions={"evil.scope": True},
            position=0,
        )
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "require_admin_permission", return_value=1),
            patch("src.api.get_db"),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(role_routes.create_role(_request(), body))
        assert exc.value.status_code == 400

    def test_update_role_rejects_subscope_wildcard(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleUpdate

        hierarchy = {
            "current_pos": 50,
            "target_pos": 50,
            "admin_exists": True,
            "role_pos": 10,
            "role_is_system": False,
        }
        body = RoleUpdate(description="test", permissions={"reports.*": True})
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "require_admin_permission", return_value=1),
            patch.object(role_routes, "_load_hierarchy", return_value=hierarchy),
            patch("src.api.get_db"),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(role_routes.update_role(_request("PUT"), SnowflakeIDParam(1), body))
        assert exc.value.status_code == 400
