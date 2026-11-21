"""Bot tokens must not wield their owner's role-management authority.

A bot token's ``TokenInfo.user_id`` is its *human owner*; the bot's own
identity lives in ``account_id``.  The server role-management routes gate
``roles.manage`` through ``servers.require_permission(current_user.user_id,
...)`` -- i.e. against the *owner's* server roles.  A server owner receives
every server permission, so a bot holding only the narrow default bot scope
(no management permission at all) could create, rename and delete roles in
any server its owner owns merely by presenting its token.

This is the same bot-vs-user identity confusion already closed for the
automod and icon-upload routes.  These tests pin the fail-closed gate on the
role routes: a bot token is rejected with 403 before any server/permission
lookup, while a user token is still allowed through (so the guard is not an
unconditional deny).
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.servers import roles as roles_routes
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="bot" if token_type == "bot" else "standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Bot owned by user 100, whose own account is 500.  Server 42 is owned by
# user 100, so ``require_permission(current_user.user_id=100, 42, ...)``
# grants the owner's "all permissions" set if the bot reaches the gate.
_BOT = _token(100, token_type="bot", account_id=500)

# (handler, kwargs) for every privileged role-management entry point.  The
# listing route's live gate is tested too, since it also keys off
# ``current_user.user_id``.
_HANDLERS = [
    ("_require_roles_manage", {"server_id": "42"}),
    ("get_server_roles", {"server_id": "42"}),
    ("create_role", {"server_id": "42", "body": object()}),
    ("update_role", {"server_id": "42", "role_id": "7", "body": object()}),
    ("delete_role", {"server_id": "42", "role_id": "7"}),
]


@pytest.fixture
def server_lookup_spy(monkeypatch):
    """Fail loudly if a handler reaches any server/permission lookup."""

    def _unexpected():
        raise AssertionError(
            "bot token reached a server/permission lookup instead of the gate"
        )

    monkeypatch.setattr(api, "get_servers", _unexpected)
    return _unexpected


@pytest.mark.parametrize("handler,kwargs", _HANDLERS)
def test_bot_token_rejected_before_server_lookup(handler, kwargs, server_lookup_spy):
    with pytest.raises(HTTPException) as exc:
        _call(getattr(roles_routes, handler), current_user=_BOT, **kwargs)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


@pytest.mark.parametrize("handler,kwargs", _HANDLERS)
def test_user_token_passes_the_gate(handler, kwargs, monkeypatch):
    """A user token is not rejected (non-vacuous control).

    With no servers module available the handler proceeds to its normal 500
    "module not available" path, proving the 403 is specific to bot tokens.
    """
    monkeypatch.setattr(api, "get_servers", lambda: None)

    with pytest.raises(HTTPException) as exc:
        _call(getattr(roles_routes, handler), current_user=_token(100), **kwargs)

    assert exc.value.status_code == 500
