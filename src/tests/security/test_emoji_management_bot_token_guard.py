"""Emoji management must not be driven by a bot token.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id is
in ``account_id``). All three mutating handlers in ``routes/emojis.py`` pass
``current_user.user_id`` straight into ``_require_manage_emojis``, which calls
``servers.get_permissions(user_id, server_id)`` and treats that id as the acting
member; the core create/update/delete operations get the same owner id.

Without an explicit ``require_user_token`` gate, a narrow-scope bot credential
therefore wields the owner's server roles: it can create, rename and delete
custom emojis in any server its owner manages -- actions its own configured
permission set never granted (the default bot scope has no ``emojis.manage``
permission at all). This is the same owner-identity confusion already rejected
on the ban list, roles and member routes, but on an emoji/media mutation
surface that was never gated.

The runtime checks below are non-vacuous: before the guard was added the bot
token reached (and would have passed) the backend permission lookup and these
tests failed. The control test proves the sentinel still fires for a real user
token. Run with::

    pytest src/tests/security/test_emoji_management_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes import emojis as emoji_routes
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={"messages.send": True},
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


# Bot 500 is owned by user 100. Without the guard every handler resolves the
# owner's identity (user 100) and wields whatever server authority that owner
# has.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")

_ROUTE_KWARGS = {"server_id": "42"}
_EMOJI_KWARGS = {"server_id": "42", "emoji_id": "7"}
# Backend objects are never read before the gate; a bare Mock is sufficient.
_HANDLERS = [
    ("create_emoji", {**_ROUTE_KWARGS, "name": "x", "image": Mock()}),
    ("update_emoji", {**_EMOJI_KWARGS, "body": Mock()}),
    ("delete_emoji", dict(_EMOJI_KWARGS)),
]
_HANDLER_IDS = [name for name, _ in _HANDLERS]


class _ReachedBackend(BaseException):
    """Sentinel raised by a patched backend to stop the handler right there.

    Derives from ``BaseException`` so a handler's broad ``except Exception``
    cannot turn a proven backend lookup into a generic 500.
    """


@pytest.fixture
def backend_sentinel(monkeypatch):
    """Stop the handler the moment it reaches any backend lookup."""

    def _unexpected(*args, **kwargs):
        raise _ReachedBackend()

    monkeypatch.setattr(emoji_routes.api, "get_reactions", _unexpected)
    monkeypatch.setattr(emoji_routes.api, "get_servers", _unexpected)
    return _unexpected


@pytest.mark.parametrize("name,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_bot_token_refused_before_backend_lookup(name, kwargs, backend_sentinel):
    handler = getattr(emoji_routes, name)
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_BOT, **kwargs)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


@pytest.mark.parametrize("name,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_user_token_passes_the_identity_gate(name, kwargs, backend_sentinel):
    """Control: a human session is not rejected by the identity gate."""
    handler = getattr(emoji_routes, name)
    with pytest.raises(_ReachedBackend):
        _call(handler, current_user=_USER, **kwargs)


@pytest.mark.parametrize("name,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_handler_source_calls_require_user_token(name, kwargs):
    """Static guard so a future edit cannot silently drop the runtime check."""
    handler = getattr(emoji_routes, name)
    assert "require_user_token(current_user)" in inspect.getsource(handler)
