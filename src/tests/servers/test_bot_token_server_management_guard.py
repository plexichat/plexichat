"""Bot tokens must not wield their owner's server-management authority.

A bot token's ``TokenInfo.user_id`` is its *owner*; the bot's own identity is
in ``account_id``.  The server-management routes gated ``roles.manage`` /
``server.manage`` / ``server.automod`` through
``servers.require_permission(current_user.user_id, ...)`` -- i.e. against the
owner's server roles.  A server owner receives every server permission, so a
bot holding only narrow global permissions (the default bot set has no
management scopes at all) could create/delete automod rules, read violation
records and upload/replace the server icon merely by presenting its token.
That is a bot-vs-user identity confusion in an authorization check: the bot's
own identity/permissions were never consulted.

These routes now reject bot credentials up front with ``require_user_token``.
The tests assert the rejection happens *before* any server/permission lookup
and that a user token is still allowed past the gate, so the guard is neither
vacuous nor an unconditional deny.
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.servers import automod as automod_routes
from src.api.routes.servers import icon_upload as icon_routes
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


# Bot owned by user 100; the bot's own account is 500. Server 42 is owned by
# user 100, so ``require_permission(current_user.user_id=100, 42, ...)`` would
# grant the owner's "all permissions" set if the bot were allowed to reach it.
_BOT = _token(100, token_type="bot", account_id=500)

# (module, handler, kwargs) for every privileged server-management handler.
_HANDLERS = [
    (automod_routes, "get_server_automod_rules", {"server_id": "42"}),
    (
        automod_routes,
        "create_server_automod_rule",
        {"server_id": "42", "body": object()},
    ),
    (
        automod_routes,
        "update_server_automod_rule",
        {"server_id": "42", "rule_id": "7", "body": object()},
    ),
    (
        automod_routes,
        "delete_server_automod_rule",
        {"server_id": "42", "rule_id": "7"},
    ),
    (automod_routes, "get_server_automod_violations", {"server_id": "42"}),
    (
        automod_routes,
        "get_server_automod_violation",
        {"server_id": "42", "violation_id": "9"},
    ),
    (icon_routes, "upload_server_icon", {"server_id": "42", "file": object()}),
]


@pytest.fixture
def server_lookup_spy(monkeypatch):
    """Fail loudly if a handler reaches any server/permission lookup."""

    def _unexpected():
        raise AssertionError(
            "bot token reached server lookup instead of being rejected at the gate"
        )

    monkeypatch.setattr(api, "get_servers", _unexpected)
    monkeypatch.setattr(api, "get_media", _unexpected)
    return _unexpected


@pytest.mark.parametrize("module,handler,kwargs", _HANDLERS)
def test_bot_token_rejected_before_server_lookup(
    module, handler, kwargs, server_lookup_spy
):
    with pytest.raises(HTTPException) as exc:
        _call(getattr(module, handler), current_user=_BOT, **kwargs)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


@pytest.mark.parametrize("module,handler,kwargs", _HANDLERS)
def test_user_token_passes_the_gate(module, handler, kwargs, monkeypatch):
    """A user token is not rejected by the bot gate (non-vacuous control).

    With no servers module available the handler proceeds to its normal 500
    "module not available" path. That proves the 403 above is specific to bot
    credentials rather than an unconditional denial of every request.
    """
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_media", lambda: None)

    with pytest.raises(HTTPException) as exc:
        _call(getattr(module, handler), current_user=_token(100), **kwargs)

    assert exc.value.status_code == 500
