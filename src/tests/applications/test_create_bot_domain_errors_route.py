"""Route must translate create-bot domain errors, not leak a raw 500.

``POST /applications/{application_id}/bot`` calls
``create_bot_for_application`` -> ``auth.create_bot``. The core raises
``UserExistsError`` on a username collision (and ``BotLimitExceededError``
when the owner's bot quota is full). Both are expected, user-facing conflict
outcomes, but the route had no ``except`` arm for either: they fell through to
``except Exception -> 500``, so a caller could not distinguish a real collision
from a server fault. This pins the mapping to 409/429 with static messages
(the raw ``str(exc)`` never reaches the client).
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.schemas.common import SnowflakeID
from src.core.auth.exceptions import BotLimitExceededError, UserExistsError
from src.core.auth.models import AccountType


def _token(user_id=100):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=SnowflakeID(str(user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _raise(exc):
    def fake_create_bot_for_application(**kwargs):
        raise exc

    return fake_create_bot_for_application


def test_create_bot_user_exists_is_conflict_not_500(monkeypatch):
    from src.api.routes import applications as routes

    # The core message embeds internal detail (the requested bot name); the
    # client must receive a stable message, never str(exc).
    monkeypatch.setattr(
        routes.applications,
        "create_bot_for_application",
        _raise(UserExistsError("Bot creation failed for internal_name")),
    )

    with pytest.raises(HTTPException) as exc:
        _call(routes.create_bot, application_id="123", current_user=_token())

    assert exc.value.status_code == 409
    body = exc.value.detail["error"]
    assert body["code"] == 409
    assert "internal_name" not in str(body)


def test_create_bot_limit_exceeded_is_mapped(monkeypatch):
    from src.api.routes import applications as routes

    monkeypatch.setattr(
        routes.applications,
        "create_bot_for_application",
        _raise(BotLimitExceededError("User has reached the bot limit of 5")),
    )

    with pytest.raises(HTTPException) as exc:
        _call(routes.create_bot, application_id="123", current_user=_token())

    # A quota is a client-actionable throttle, never a 500.
    assert exc.value.status_code == 429
    body = exc.value.detail["error"]
    assert body["code"] == 429
    assert "bot limit of 5" not in str(body)
