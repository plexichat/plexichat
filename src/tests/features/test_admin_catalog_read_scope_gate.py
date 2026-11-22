"""Admin config-catalog reads require the declared ``config.read`` scope.

``GET /admin/tiers`` and ``GET /admin/badges`` expose the feature rate-limit
and badge catalogs. ``get_admin_access`` synthesises ``admin.*`` for *any*
valid admin session and ``_check_admin`` only tests that wildcard, so a
narrowly-scoped admin (e.g. tickets-only support) could otherwise read another
area's configuration. The sibling admin read ``GET /admin/capabilities`` gates
system configuration on ``config.read``; these handlers now require the same
declared scope.

The tests drive the real route coroutines with a scope double and a DB double,
so they fail against the un-hardened handlers (data is read) and pass only when
the concrete scope is enforced and fails closed.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.features.tier_features import (
    get_available_badges,
    get_available_tiers,
)
from src.core.admin.permissions import DELEGABLE_PERMISSIONS, PERMISSION_SCOPES
from src.core.admin.request_roles import clear_session_roles
from src.core.auth.models import AccountType, TokenInfo

pytestmark = pytest.mark.unit

SCOPE = "config.read"
ADMIN_ID = 900


def _admin() -> TokenInfo:
    """An admin-session credential: bare wildcard membership, no role scope."""
    return TokenInfo(
        valid=True,
        token_type="admin",
        account_id=ADMIN_ID,
        user_id=ADMIN_ID,
        session_id=None,
        permissions={"administrator": True, "admin.*": True},
        rate_limit_tier="staff",
        expires_at=None,
        username="admin",
        account_type=AccountType.SYSTEM,
    )


class _ScopeDB:
    """DB double: admin role payloads decide the ``config.read`` grant."""

    def __init__(self, *, role_permissions=None):
        self.role_permissions = role_permissions or {}

    def fetch_all(self, sql, params=()):
        if "admin_roles" in sql and "admin_role_assignments" in sql:
            uid = int(cast(Any, params)[0])
            return [
                {
                    "id": idx + 1,
                    "name": f"role-{idx}",
                    "description": "",
                    "permissions": json.dumps(payload),
                    "created_at": 0,
                    "created_by": 0,
                    "is_system": 0,
                }
                for idx, payload in enumerate(self.role_permissions.get(uid, []))
            ]
        return []

    def fetch_one(self, sql, params=()):
        return None


class _Limits:
    def to_dict(self):
        return {
            "name": "free",
            "multiplier": 1.0,
            "max_voice_minutes_per_day": 1,
            "max_video_minutes_per_day": 1,
            "max_file_uploads_per_day": 1,
            "max_file_size_mb": 1,
            "max_storage_mb": 1,
            "max_servers": 1,
            "max_message_length": 1,
            "max_reactions_per_message": 1,
            "max_pins_per_channel": 1,
            "custom_emoji_slots": 1,
        }


def _features():
    return SimpleNamespace(
        get_tier_configuration=lambda: SimpleNamespace(
            limits={"free": _Limits()}, default="free"
        )
    )


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    holder: dict[str, Any] = {}
    monkeypatch.setattr(api, "get_db", lambda: holder.get("db"))
    reads: list[str] = []

    def _features_module():
        reads.append("features")
        return _features()

    monkeypatch.setattr(
        "src.api.routes.features.tier_features._get_features_module",
        _features_module,
    )
    holder["reads"] = reads
    clear_session_roles()
    return holder


def _db_with_scopes(scopes):
    return _ScopeDB(role_permissions={ADMIN_ID: [scopes]})


HANDLERS = [
    pytest.param(get_available_tiers, id="get_available_tiers"),
    pytest.param(get_available_badges, id="get_available_badges"),
]


def test_scope_is_declared_and_delegable():
    assert SCOPE in PERMISSION_SCOPES
    assert SCOPE in DELEGABLE_PERMISSIONS


@pytest.mark.parametrize("handler", HANDLERS)
def test_narrow_admin_denied_before_catalog_read(_wire, handler):
    """Bare admin membership must not expose another area's config."""
    _wire["db"] = _db_with_scopes({"tickets.read": True})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler(_admin()))

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    assert _wire["reads"] == [], "catalog was read despite missing config.read scope"


@pytest.mark.parametrize("handler", HANDLERS)
def test_scoped_admin_reaches_catalog(_wire, handler):
    """A concrete config.read grant reaches the data layer."""
    _wire["db"] = _db_with_scopes({SCOPE: True})

    result = asyncio.run(handler(_admin()))

    assert result is not None
    assert _wire["reads"] == ["features"]


@pytest.mark.parametrize("handler", HANDLERS)
def test_unavailable_database_fails_closed(_wire, handler):
    _wire["db"] = None

    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler(_admin()))

    assert exc.value.status_code == 500
    assert _wire["reads"] == []
