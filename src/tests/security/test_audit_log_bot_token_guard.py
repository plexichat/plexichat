"""Server audit-log reads must not be driven by a bot token.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id is
in ``account_id``). ``get_audit_log`` passes ``current_user.user_id`` straight
into ``servers.get_audit_log``, and the core treats that id as the acting
moderator: it checks ``server.view_audit_log`` against it. Without an explicit
``require_user_token`` gate, a narrow-scope bot credential therefore reads the
moderation/audit history of any server its owner manages -- the same
owner-identity confusion already guarded on the ban list.

These tests verify the gate is present and actually refuses the bot token
before any backend lookup, while a normal user token still reaches the handler.
Run with::

    pytest src/tests/security/test_audit_log_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.servers import audit_log as audit_routes
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


# Bot 500 is owned by user 100. Without the guard the handler resolves the
# owner's identity (user 100) and wields whatever audit authority that owner has.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")
_ROUTE_KWARGS = {"server_id": "42"}


@pytest.fixture
def no_backend_lookup(monkeypatch):
    """Fail loudly if the handler reaches a server/auth lookup."""

    def _unexpected(*args, **kwargs):
        raise AssertionError("bot token reached a backend lookup instead of the gate")

    monkeypatch.setattr(api, "get_servers", _unexpected)
    monkeypatch.setattr(api, "get_auth", _unexpected)
    return _unexpected


def test_bot_token_refused_before_backend_lookup(no_backend_lookup):
    with pytest.raises(HTTPException) as exc:
        _call(audit_routes.get_audit_log, current_user=_BOT, **_ROUTE_KWARGS)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_user_token_passes_the_identity_gate(monkeypatch):
    """Control: a user token is not rejected by the identity gate."""
    monkeypatch.setattr(api, "get_servers", lambda: None)
    with pytest.raises(HTTPException) as exc:
        _call(audit_routes.get_audit_log, current_user=_USER, **_ROUTE_KWARGS)
    # It got past the gate to the "servers module unavailable" 500 check.
    assert exc.value.status_code == 500


def test_bot_token_does_not_wield_owner_authority(monkeypatch):
    servers = Mock()
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_auth", lambda: None)

    with pytest.raises(HTTPException) as exc:
        _call(audit_routes.get_audit_log, current_user=_BOT, **_ROUTE_KWARGS)
    assert exc.value.status_code == 403
    servers.get_audit_log.assert_not_called()


def test_handler_source_calls_require_user_token():
    """Static guard so the runtime checks cannot silently stop covering it."""
    body = inspect.getsource(audit_routes.get_audit_log)
    assert "require_user_token(current_user)" in body
