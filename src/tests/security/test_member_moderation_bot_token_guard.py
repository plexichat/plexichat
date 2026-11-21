"""Bot credentials must not wield their owner's server moderation authority.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id is
in ``account_id``).  The kick/ban routes pass ``current_user.user_id`` straight
into ``servers.kick_member`` / ``ban_member`` / ``unban_member``, which treat it
as the acting member.  A narrow-scope bot credential therefore acts as its
owner: if that owner owns the server, the bot can kick or ban any member, and
read the ban list, without holding any management permission itself.

The role-management routes already reject bot credentials with
``require_user_token`` for exactly this reason.  These tests pin the same
identity boundary on the member-moderation routes: a bot token is refused
before any server/database lookup, and a user token still reaches the normal
handler (so the guard is specific to bots, not an unconditional deny).

Run with: pytest src/tests/security/test_member_moderation_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.servers import bans as bans_routes
from src.api.routes.servers import members as members_routes
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


# Bot 500 is owned by user 100. Server 42 is owned by user 100, so without the
# guard every handler below would resolve the *owner's* server authority.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")

_HANDLERS = [
    ("kick_member", members_routes.kick_member, {"server_id": "42", "member_id": "7"}),
    ("ban_member", bans_routes.ban_member, {"server_id": "42", "user_id": "7"}),
    ("unban_member", bans_routes.unban_member, {"server_id": "42", "user_id": "7"}),
    ("get_server_bans", bans_routes.get_server_bans, {"server_id": "42"}),
]


@pytest.fixture
def no_backend_lookup(monkeypatch):
    """Fail loudly if any handler reaches a server/database lookup."""

    def _unexpected(*args, **kwargs):
        raise AssertionError("bot token reached a backend lookup instead of the gate")

    monkeypatch.setattr(api, "get_servers", _unexpected)
    monkeypatch.setattr(api, "get_db", _unexpected)
    return _unexpected


@pytest.mark.parametrize(
    "name,handler,kwargs", _HANDLERS, ids=[h[0] for h in _HANDLERS]
)
def test_bot_token_refused_before_backend_lookup(
    name, handler, kwargs, no_backend_lookup
):
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_BOT, **kwargs)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


@pytest.mark.parametrize(
    "name,handler,kwargs", _HANDLERS, ids=[h[0] for h in _HANDLERS]
)
def test_user_token_passes_the_gate(name, handler, kwargs, monkeypatch):
    """Control: a user token is not rejected by the identity gate."""
    monkeypatch.setattr(api, "get_servers", lambda: None)
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_USER, **kwargs)
    assert exc.value.status_code == 500


def _mock_backends(monkeypatch):
    servers = Mock()
    db = Mock()
    db.fetch_one.return_value = {"user_id": 7}
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_db", lambda: db)
    return servers


def test_bot_token_does_not_invoke_kick_as_owner(monkeypatch):
    servers = _mock_backends(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _call(
            members_routes.kick_member, current_user=_BOT, server_id="42", member_id="7"
        )
    assert exc.value.status_code == 403
    servers.kick_member.assert_not_called()


def test_bot_token_does_not_invoke_ban_as_owner(monkeypatch):
    servers = _mock_backends(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _call(bans_routes.ban_member, current_user=_BOT, server_id="42", user_id="7")
    assert exc.value.status_code == 403
    servers.ban_member.assert_not_called()
