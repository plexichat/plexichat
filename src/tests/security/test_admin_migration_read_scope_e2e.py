"""End-to-end behavioral pin for the admin migration read-scope fix (b94d7209).

The sibling module ``test_admin_migration_read_scope.py`` stubs
``require_admin_permission`` itself, so it proves only that each GET handler
*calls* the gate. It cannot catch a real permission-core regression (e.g. a
wildcard/fallback that grants every scope, or a scope that is declared but not
actually delegable). This module drives the genuine chain --

    require_admin_permission -> check_admin_permission -> check_permission

-- against a role DB double and an authenticated Request, replacing only the
bearer validation, the host check and the DB handle. Role payloads are built
through ``validate_delegable_permissions``, the production chokepoint, so the
scope's declaration and delegability are exercised too.

Non-vacuity:
  * drop the route gate -> the narrow admin reaches the data layer and the
    expected 403 is not raised;
  * drop the PERMISSION_SCOPES declaration -> the scoped payload cannot be
    delegated and this module fails during collection.
"""

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

import src.api as api
from src.api.routes.admin import migrations as migrations_routes
from src.api.routes.admin import utils as admin_utils
from src.core import admin as admin_core
from src.core.admin import request_roles
from src.core.admin.permissions import validate_delegable_permissions

SCOPE = "admin.migrations.read"
NARROW_ADMIN_ID = 84001
SCOPED_ADMIN_ID = 84002

pytestmark = [pytest.mark.security, pytest.mark.unit]


# Built through the production chokepoint: an undeclared, hence non-delegable,
# scope raises ValueError at collection instead of silently granting access.
_NARROW_PAYLOAD = validate_delegable_permissions(
    {"admin.migrations.execute": True, "admin.audit.read": True}
)
_SCOPED_PAYLOAD = validate_delegable_permissions({SCOPE: True})

_TOKEN_TO_ADMIN = {"narrow": NARROW_ADMIN_ID, "scoped": SCOPED_ADMIN_ID}


class _NoTouchManager:
    """Explodes if a handler reaches migration data before the gate refuses."""

    def __getattr__(self, name):
        raise AssertionError(f"migration data read before scope gate: {name}")


class _GatePassed(BaseException):
    """Marker proving the real gate passed and data access was reached.

    Inherits BaseException so the handlers' broad ``except Exception`` error
    translation cannot swallow it.
    """

    def __init__(self, name):
        self.name = name
        super().__init__(name)


class _SentinelManager:
    """Raises the marker as soon as any migration data is touched."""

    def __getattr__(self, name):
        raise _GatePassed(name)


class _RoleDB:
    """Minimal DB double: only the admin-role lookup is needed by the gate."""

    def __init__(self):
        self.lookups = []

    def fetch_all(self, sql, params=()):
        admin_id = params[0] if params else None
        self.lookups.append(admin_id)
        payload = {
            NARROW_ADMIN_ID: _NARROW_PAYLOAD,
            SCOPED_ADMIN_ID: _SCOPED_PAYLOAD,
        }.get(admin_id)
        if payload is None:
            return []
        return [
            {
                "id": 1,
                "name": "delegated-role",
                "description": "",
                "permissions": payload,
                "created_at": 0,
                "created_by": 0,
                "is_system": 0,
            }
        ]

    def fetch_one(self, sql, params=()):
        return None


def _request(token: str) -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/migrations",
            "headers": [(b"authorization", b"Bearer " + token.encode("ascii"))],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


HANDLERS = [
    pytest.param(migrations_routes.list_migrations, (), id="list_migrations"),
    pytest.param(
        migrations_routes.get_migration_system_status,
        (),
        id="get_migration_system_status",
    ),
    pytest.param(
        migrations_routes.get_migration_details, ("001",), id="get_migration_details"
    ),
]


@pytest.mark.parametrize("handler,args", HANDLERS)
def test_real_permission_chain_enforces_delegated_read_scope(
    monkeypatch, handler, args
):
    """Real checker denies a narrow admin and admits a delegated scoped admin."""
    db = _RoleDB()
    request_roles.clear_session_roles()
    monkeypatch.setattr(
        admin_core, "validate_session", lambda token: _TOKEN_TO_ADMIN[token]
    )
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(
        admin_utils, "is_authenticated_self_test", lambda request: False
    )
    monkeypatch.setattr(migrations_routes, "check_host_restriction", lambda r: None)

    # Narrow admin: holds the migration *write* scope and an unrelated read
    # scope, but NOT the read scope under test. The real permission checker
    # must refuse with 403 before any migration data is touched.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            handler(
                _request("narrow"),
                *args,
                manager=_NoTouchManager(),
                admin_user=NARROW_ADMIN_ID,
            )
        )
    assert exc.value.status_code == 403

    # Scoped admin: the read scope was delegated through the real chokepoint,
    # so the same real gate must let the request reach the data layer.
    with pytest.raises(_GatePassed):
        asyncio.run(
            handler(
                _request("scoped"),
                *args,
                manager=_SentinelManager(),
                admin_user=SCOPED_ADMIN_ID,
            )
        )

    # Both decisions came from real role lookups for the intended admins, so
    # neither arm can pass merely because no role row was found.
    assert NARROW_ADMIN_ID in db.lookups
    assert SCOPED_ADMIN_ID in db.lookups
