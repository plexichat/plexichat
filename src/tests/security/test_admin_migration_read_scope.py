"""N730 regression: migration READs require a concrete permission scope.

The three GET handlers (``list_migrations``, ``get_migration_system_status``
and ``get_migration_details``) expose applied/pending/failed migration state,
integrity results, metadata and execution logs. They carried only
``Depends(get_admin_user)``, which validates the admin *session* and (since the
demotion fix) that at least one live scope exists -- but never checked that the
holder carries the migration *read* scope. Any low-privilege admin with a
valid session could therefore enumerate all of it.

The handlers now call ``require_admin_permission(request,
"admin.migrations.read")`` before touching any data. These tests pin the
declared/delegable scope, the literal gate in each handler, and -- critically --
that a narrow admin is refused before any migration data can be read.
"""

import asyncio
import inspect
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from src.api.routes.admin import migrations as migrations_routes
from src.core.admin.permissions import DELEGABLE_PERMISSIONS, PERMISSION_SCOPES

SCOPE = "admin.migrations.read"

pytestmark = [pytest.mark.security, pytest.mark.unit]


def _request() -> Request:
    # The handlers only pass this through to the (patched) auth helpers.
    return cast(Request, SimpleNamespace())


class _NoTouchManager:
    """Manager that explodes if any data method is reached before the gate."""

    def __getattr__(self, name):
        raise AssertionError(f"migration data read before scope gate: {name}")


class _GatePassed(BaseException):
    """Marker proving the scope gate was passed and data access was reached.

    Derived from BaseException so the handlers' broad ``except Exception``
    error translation cannot swallow it.
    """


class _SentinelManager:
    """Manager that raises a marker as soon as data access begins."""

    def __getattr__(self, name):
        raise _GatePassed(name)


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


def test_scope_is_declared_and_delegable():
    assert SCOPE in PERMISSION_SCOPES
    assert SCOPE in DELEGABLE_PERMISSIONS


@pytest.mark.parametrize("handler,args", HANDLERS)
def test_read_handler_declares_concrete_scope(handler, args):
    """Pin the gate so a future refactor cannot silently drop the scope."""
    source = inspect.getsource(handler)
    assert f'require_admin_permission(request, "{SCOPE}")' in source


@pytest.mark.parametrize("handler,args", HANDLERS)
def test_narrow_admin_is_denied_before_any_data_read(monkeypatch, handler, args):
    """A valid admin token with the wrong scope must be refused before any read."""
    calls = []

    def _deny(request, permission):
        calls.append(permission)
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    monkeypatch.setattr(migrations_routes, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(migrations_routes, "require_admin_permission", _deny)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            handler(_request(), *args, manager=_NoTouchManager(), admin_user=1)
        )
    assert exc.value.status_code == 403
    assert calls == [SCOPE]


@pytest.mark.parametrize("handler,args", HANDLERS)
def test_scoped_admin_reaches_data_layer(monkeypatch, handler, args):
    """A correctly scoped admin gets past the gate to the data layer."""
    calls = []

    def _allow(request, permission):
        calls.append(permission)
        return 7

    monkeypatch.setattr(migrations_routes, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(migrations_routes, "require_admin_permission", _allow)

    with pytest.raises(_GatePassed):
        asyncio.run(
            handler(_request(), *args, manager=_SentinelManager(), admin_user=1)
        )
    assert calls == [SCOPE]
