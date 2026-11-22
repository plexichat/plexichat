"""Route must translate create-application domain errors, not leak a raw 500.

``POST /applications`` (``create_application``) calls
``applications.create_application``. The core raises ``ApplicationLimitError``
when the owner's application quota is full (and ``InvalidApplicationNameError``
for a name that passes client validation but fails the core policy). Both are
client-actionable outcomes: the global ``ERROR_MAPPINGS`` names ``LimitError``
as a 400, and the sibling ``BotLimitError`` route already returns 400. But this
route had no ``except`` arm for either, so they fell through to
``except Exception -> 500``: a caller could not distinguish a quota/name
problem from a server fault, and the error log echoed ``str(exc)`` verbatim.
This pins the mapping to 400 with a static body; the raw ``str(exc)`` (which
carries internal quota/policy detail) never reaches the client.
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.schemas.common import SnowflakeID
from src.core.applications.exceptions import (
    ApplicationLimitError,
    InvalidApplicationNameError,
)
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
    def fake_create_application(**kwargs):
        raise exc

    return fake_create_application


def test_create_application_limit_is_400_not_500(monkeypatch):
    from src.api.routes import applications as routes

    # The core message embeds the configured quota and internal detail; the
    # client must receive a stable message, never str(exc).
    monkeypatch.setattr(
        routes.applications,
        "create_application",
        _raise(ApplicationLimitError("quota full INTERNAL_DETAIL max=25", 25, 25)),
    )

    request_body = routes.CreateApplicationRequest(name="test")
    with pytest.raises(HTTPException) as exc:
        _call(
            routes.create_application,
            body=request_body,
            current_user=_token(),
        )

    # A quota is a client-actionable outcome, never a 500.
    assert exc.value.status_code == 400
    body = exc.value.detail["error"]
    assert body["code"] == 400
    assert "INTERNAL_DETAIL" not in str(body)


def test_create_application_invalid_name_is_400_not_500(monkeypatch):
    from src.api.routes import applications as routes

    monkeypatch.setattr(
        routes.applications,
        "create_application",
        _raise(InvalidApplicationNameError("policy rejected SECRET_PATTERN")),
    )

    request_body = routes.CreateApplicationRequest(name="test")
    with pytest.raises(HTTPException) as exc:
        _call(
            routes.create_application,
            body=request_body,
            current_user=_token(),
        )

    assert exc.value.status_code == 400
    body = exc.value.detail["error"]
    assert body["code"] == 400
    assert "SECRET_PATTERN" not in str(body)
