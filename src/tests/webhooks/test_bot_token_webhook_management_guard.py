"""Bot tokens must not wield their owner's webhook-management authority.

A bot token's ``TokenInfo.user_id`` is its *human owner*; the bot's own
identity is in ``account_id``. Every webhook management handler authorized
against ``current_user.user_id`` through the webhooks core
(``_check_manage_webhooks_permission(user_id, server_id, channel_id)``), which
checks that user's server roles. A server owner has ``webhooks.manage`` on
their servers, so a bot holding only its narrow global token could create,
regenerate, update and delete webhooks on any server its owner manages -- the
owner's authority wrongly inherited by the bot. The bot's own identity and
permissions were never consulted.

These routes now reject bot credentials up front with ``require_user_token``.
The tests assert rejection happens *before* any webhook module lookup and that
a user token is still allowed past the gate, so the guard is neither vacuous
nor an unconditional deny.
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes import webhooks as webhook_routes
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


# Bot owned by user 100; the bot's own account is 500. The webhooks core would
# authorize against ``current_user.user_id=100`` (the owner), so the bot must
# never reach that lookup.
_BOT = _token(100, token_type="bot", account_id=500)

# (handler, kwargs) for every webhook management handler that authorizes on the
# caller's identity. ``execute_webhook`` is excluded: it is a public,
# token-authenticated route with no ``current_user``.
_HANDLERS = [
    ("regenerate_webhook_token", {"webhook_id": "42"}),
    ("create_webhook", {"body": object()}),
    ("get_webhook", {"webhook_id": "42"}),
    ("update_webhook", {"webhook_id": "42", "body": object()}),
    ("delete_webhook", {"webhook_id": "42"}),
]


@pytest.fixture
def webhook_lookup_spy(monkeypatch):
    """Fail loudly if a handler reaches a webhook lookup before the gate."""

    def _unexpected():
        raise AssertionError(
            "bot token reached webhook module lookup instead of being rejected "
            "at the user-token gate"
        )

    monkeypatch.setattr(api, "get_webhooks", _unexpected)


@pytest.mark.parametrize("handler,kwargs", _HANDLERS)
def test_bot_token_rejected_before_webhook_lookup(handler, kwargs, webhook_lookup_spy):
    with pytest.raises(HTTPException) as exc:
        _call(getattr(webhook_routes, handler), current_user=_BOT, **kwargs)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


@pytest.mark.parametrize("handler,kwargs", _HANDLERS)
def test_user_token_passes_the_gate(handler, kwargs, monkeypatch):
    """A user token is not rejected by the bot gate (non-vacuous control).

    With no webhooks module available the handler proceeds to its normal 500
    "module not available" path. That proves the 403 above is specific to bot
    credentials rather than an unconditional denial of every request.
    """
    monkeypatch.setattr(api, "get_webhooks", lambda: None)

    with pytest.raises(HTTPException) as exc:
        _call(getattr(webhook_routes, handler), current_user=_token(100), **kwargs)

    assert exc.value.status_code == 500
