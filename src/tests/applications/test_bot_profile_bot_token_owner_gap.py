"""A bot credential must not act as its owner on the private bot-profile read.

A bot token's ``user_id`` is its *human owner* (the bot account id lives in
``account_id``). ``GET /bots/profiles/{application_id}`` has optional auth and
passes the caller's ``user_id`` to the core as ``viewer_id``, which is the
owner check for private profiles. Before the fix a leaked bot token was
therefore treated as the owner and could read the private bot profiles of
every application owned by that human. Every other ``current_user.user_id``
handler in ``bots.py`` already calls ``require_user_token``; this optional-auth
route was the one gap.

The regression pins the viewer identity the route hands to the core: a real
user token keeps its owner identity; bot credentials and anonymous callers are
downgraded to ``None`` so private profiles stay indistinguishable from missing.
"""

import asyncio
import importlib
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Bot token whose ``user_id`` is the owner's id; the bot account itself is 500.
_BOT = _token(100, token_type="bot", account_id=500)
_OWNER = _token(100)


def _stub_core(monkeypatch):
    """Replace the core lookup and record the viewer identity it receives."""
    route = importlib.import_module("src.api.routes.bots")
    seen = {}

    def fake_get_bot_profile(application_id, viewer_id=None):
        seen["application_id"] = application_id
        seen["viewer_id"] = viewer_id
        # Emulate the core: only the owner (viewer_id == 100) sees the row.
        if viewer_id is not None and str(viewer_id) == "100":
            return SimpleNamespace(
                application_id=1,
                description="desc",
                short_description="short",
                avatar_url=None,
                banner_url=None,
                website_url=None,
                support_url=None,
                github_url=None,
                tags=[],
                nsfw=False,
                private=True,
            )
        return None

    monkeypatch.setattr(route.applications, "get_bot_profile", fake_get_bot_profile)
    return route, seen


def test_bot_token_cannot_read_owner_private_profile(monkeypatch):
    route, seen = _stub_core(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        _call(route.get_bot_profile, application_id=1, current_user=_BOT)

    assert exc.value.status_code == 404
    # Before the fix this was the owner's id (100), leaking the private row.
    assert seen["viewer_id"] is None


def test_user_token_retains_owner_identity(monkeypatch):
    route, seen = _stub_core(monkeypatch)

    # A real user token must still resolve its own private profile.
    result = _call(route.get_bot_profile, application_id=1, current_user=_OWNER)

    assert result is not None
    assert str(seen["viewer_id"]) == "100"


def test_anonymous_caller_is_not_owner(monkeypatch):
    route, seen = _stub_core(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        _call(route.get_bot_profile, application_id=1, current_user=None)

    assert exc.value.status_code == 404
    assert seen["viewer_id"] is None
