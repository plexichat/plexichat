"""Member-mutation routes must not be driven by a bot token.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id is
in ``account_id``).  Three handlers in ``servers.members`` pass
``current_user.user_id`` straight into a servers operation that treats it as
the acting member:

* ``assign_role_to_member``   -> ``servers.assign_role``
* ``remove_role_from_member`` -> ``servers.remove_role``
* ``update_server_member``    -> ``servers.update_member``

Without an explicit ``require_user_token`` gate a narrow-scope bot credential
therefore acts as its owner: if that owner manages (or owns) the server, the
bot can grant/revoke privileged roles or rename/mute/timeout members - a
privilege-escalation path.

These tests verify the gate is present on all three handlers and actually
refuses the bot token before any backend lookup, while a normal user token
still reaches the handler. Run with::

    pytest src/tests/security/test_member_role_assignment_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import src.api as api
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


# Bot 500 is owned by user 100. Without the guard the handlers resolve the
# owner's identity (user 100) and wield whatever authority that owner has.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")
_ROLE_KWARGS = {"server_id": "42", "member_id": "7", "role_id": "9"}
# ``payload`` is never read before the gate, so a bare Mock is sufficient.
_UPDATE_KWARGS = {"server_id": "42", "member_id": "7", "payload": Mock()}

# (handler, route kwargs, backend method the handler would call as the actor)
_HANDLERS = [
    (members_routes.assign_role_to_member, _ROLE_KWARGS, "assign_role"),
    (members_routes.remove_role_from_member, _ROLE_KWARGS, "remove_role"),
    (members_routes.update_server_member, _UPDATE_KWARGS, "update_member"),
]
_HANDLER_IDS = ["assign", "remove", "update"]


@pytest.fixture
def no_backend_lookup(monkeypatch):
    """Fail loudly if the handler reaches a server/database lookup."""

    def _unexpected(*args, **kwargs):
        raise AssertionError("bot token reached a backend lookup instead of the gate")

    monkeypatch.setattr(api, "get_servers", _unexpected)
    monkeypatch.setattr(api, "get_db", _unexpected)
    return _unexpected


@pytest.mark.parametrize(
    "handler,route_kwargs,backend_method", _HANDLERS, ids=_HANDLER_IDS
)
def test_bot_token_refused_before_backend_lookup(
    handler, route_kwargs, backend_method, no_backend_lookup
):
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_BOT, **route_kwargs)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


@pytest.mark.parametrize(
    "handler,route_kwargs,backend_method", _HANDLERS, ids=_HANDLER_IDS
)
def test_user_token_passes_the_identity_gate(
    handler, route_kwargs, backend_method, monkeypatch
):
    """Control: a user token is not rejected by the identity gate."""
    monkeypatch.setattr(api, "get_servers", lambda: None)
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_USER, **route_kwargs)
    # It got past the gate to the "servers module unavailable" 500 check.
    assert exc.value.status_code == 500


@pytest.mark.parametrize(
    "handler,route_kwargs,backend_method", _HANDLERS, ids=_HANDLER_IDS
)
def test_bot_token_does_not_wield_owner_authority(
    handler, route_kwargs, backend_method, monkeypatch
):
    servers = Mock()
    db = Mock()
    db.fetch_one.return_value = {"user_id": 7}
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_BOT, **route_kwargs)
    assert exc.value.status_code == 403
    getattr(servers, backend_method).assert_not_called()


@pytest.mark.parametrize(
    "handler,route_kwargs,backend_method", _HANDLERS, ids=_HANDLER_IDS
)
def test_handler_source_calls_require_user_token(handler, route_kwargs, backend_method):
    """Static guard so the runtime checks cannot silently stop covering it."""
    body = inspect.getsource(handler)
    assert "require_user_token(current_user)" in body
