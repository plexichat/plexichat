"""User-feature reads require the ``users.read`` admin scope.

``GET /admin/users/{user_id}/features`` exposes another account's rate-limit
tier, badges, expiry and feature flags. ``get_admin_access`` synthesises
``admin.*`` for *any* valid admin session and ``_check_admin`` only tests that
wildcard, so a narrowly-scoped admin (e.g. a tickets-only support admin) could
otherwise read any account's feature data. The sibling admin route
``GET /admin/users/{id}`` gates the same user data on ``users.read``.

The test loads the real route coroutine and drives it with a scope double, so
it fails against the un-hardened handler (no permission lookup, data returned)
and passes only when the granular scope is enforced and fails closed.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.features.tier_features import get_user_features
from src.core.admin.request_roles import clear_session_roles
from src.core.auth.models import AccountType, TokenInfo

pytestmark = pytest.mark.unit

TARGET_USER = "12345"
ADMIN_ID = "900"


def _admin() -> TokenInfo:
    """An admin-session credential: bare wildcard membership, no role scope."""
    return TokenInfo(
        valid=True,
        token_type="admin",
        account_id=int(ADMIN_ID),
        user_id=int(ADMIN_ID),
        session_id=None,
        permissions={"administrator": True, "admin.*": True},
        rate_limit_tier="staff",
        expires_at=None,
        username="admin",
        account_type=AccountType.SYSTEM,
    )


class _ScopeDB:
    """DB double: admin role payloads decide the ``users.read`` grant."""

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


class _Features:
    def __init__(self):
        self.calls: list[int] = []

    def get_user_features(self, user_id):
        self.calls.append(user_id)
        return SimpleNamespace(badges=["vip"], expires_at=123)

    def get_user_tier(self, user_id):
        return "premium"

    def get_tier_limits(self, tier):
        limits = {
            "name": tier,
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
        return SimpleNamespace(to_dict=lambda: limits)


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    holder: dict[str, Any] = {}
    monkeypatch.setattr(api, "get_db", lambda: holder.get("db"))
    monkeypatch.setattr(
        "src.api.routes.features.tier_features._user_exists", lambda uid: True
    )
    features = _Features()
    monkeypatch.setattr(
        "src.api.routes.features.tier_features._get_features_module",
        lambda: features,
    )
    holder["features"] = features
    clear_session_roles()
    return holder


def _call():
    return asyncio.run(get_user_features(TARGET_USER, _admin()))


def _db_with_scopes(scopes):
    return _ScopeDB(role_permissions={int(ADMIN_ID): [scopes]})


def test_admin_without_users_read_scope_is_denied(_wire):
    """Regression: bare admin membership must not expose another user's data."""
    _wire["db"] = _db_with_scopes({"tickets.read": True})

    with pytest.raises(HTTPException) as exc:
        _call()

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    assert _wire["features"].calls == [], "user data was read despite missing scope"


def test_admin_with_users_read_scope_can_read(_wire):
    _wire["db"] = _db_with_scopes({"users.read": True})

    result = _call()

    assert result.rate_limit_tier == "premium"
    assert _wire["features"].calls == [int(TARGET_USER)]


def test_unavailable_database_fails_closed(_wire):
    _wire["db"] = None

    with pytest.raises(HTTPException) as exc:
        _call()

    assert exc.value.status_code == 500
    assert _wire["features"].calls == []
