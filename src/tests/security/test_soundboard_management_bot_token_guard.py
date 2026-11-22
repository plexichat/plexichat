"""Soundboard management must not be driven by a bot token.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id is
in ``account_id``). Every mutating handler in ``routes/soundboard.py`` passes
``current_user.user_id`` into ``_require_manage_permission`` and into the core
upload/update/delete/permission writes. Without an explicit
``require_user_token`` gate, a narrow-scope bot credential therefore wields the
owner's server roles: it can upload, rename, delete and re-permission sounds in
any server its owner manages -- actions its own configured scope never granted.

The emoji and sticker management routes already reject bot credentials for
exactly this owner-identity confusion (``test_emoji_management_bot_token_guard``
and ``test_sticker_create_bot_token_guard``). The soundboard management surface
was missed.

The runtime tests below are non-vacuous: before the guard was added the bot
token reached (and would have passed) the backend permission lookup and these
tests failed; the control test proves the sentinel still fires for a real user
token. Run with::

    pytest src/tests/security/test_soundboard_management_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes import soundboard as soundboard_routes
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
_SOUND_KWARGS = {"server_id": "42", "sound_id": "7"}
_HANDLERS = [
    ("upload_sound", {**_ROUTE_KWARGS, "name": "airhorn", "file": Mock()}),
    ("update_sound", {**_SOUND_KWARGS, "body": Mock()}),
    ("delete_sound", dict(_SOUND_KWARGS)),
    (
        "set_sound_permission",
        {**_SOUND_KWARGS, "role_id": "9", "body": Mock()},
    ),
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

    monkeypatch.setattr(soundboard_routes.api, "get_soundboard", _unexpected)
    monkeypatch.setattr(soundboard_routes.api, "get_servers", _unexpected)
    return _unexpected


@pytest.mark.parametrize("name,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_bot_token_refused_before_backend_lookup(name, kwargs, backend_sentinel):
    handler = getattr(soundboard_routes, name)
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_BOT, **kwargs)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


@pytest.mark.parametrize("name,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_user_token_passes_the_identity_gate(name, kwargs, backend_sentinel):
    """Control: a human session is not rejected by the identity gate."""
    handler = getattr(soundboard_routes, name)
    with pytest.raises(_ReachedBackend):
        _call(handler, current_user=_USER, **kwargs)


@pytest.mark.parametrize("name,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_handler_source_calls_require_user_token(name, kwargs):
    """Static guard so a future edit cannot silently drop the runtime check."""
    handler = getattr(soundboard_routes, name)
    assert "require_user_token(current_user)" in inspect.getsource(handler)
