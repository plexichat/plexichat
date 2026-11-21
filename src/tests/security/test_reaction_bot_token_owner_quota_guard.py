"""Reaction mutations must not let a bot token spend its owner's rate quota.

A bot token's ``TokenInfo.user_id`` is its human *owner* (the bot's own id lives
in ``account_id``). All four mutating reaction handlers in ``routes/reactions.py``
charge the per-account rate bucket directly from ``current_user.user_id``:

* ``add_reaction``             -> route "PUT /reactions"
* ``clear_reactions_for_emoji`` -> route "DELETE /reactions/emoji/all"
* ``remove_reaction``          -> route "DELETE /reactions"
* ``clear_reactions``          -> route "DELETE /reactions/all"

With no identity gate ahead of that call, a leaked bot credential is charged to
the owner's ``user:<owner>:route:...`` bucket: the attacker drains the victim's
reaction quota and evades the bot's own, before any view/permission gate can
reject the request. This is the same owner-identity confusion already rejected
on the media, sticker, report, feedback, search and discovery rate-limited
routes.

The runtime tests below are non-vacuous: before the guards were added the bot
token reached (and was recorded by) the patched limiter and these tests failed.
Control tests prove a genuine user token still reaches the limiter.

Run with::

    pytest src/tests/security/test_reaction_bot_token_owner_quota_guard.py -q -n 0
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes import reactions as reactions_routes
from src.api.schemas.common import SnowflakeID
from src.core import ratelimit
from src.core.auth.models import AccountType

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


class _Request:
    """Minimal stand-in: the handlers read ``request.state.is_internal``."""

    def __init__(self):
        self.state = SimpleNamespace()


class _ReachedBucket(BaseException):
    """Sentinel raised by the patched limiter to stop the handler right there.

    Derives from ``BaseException`` so a broad ``except Exception`` in a handler
    cannot turn a proven bucket lookup into a generic 500.
    """


def _call(handler, **kwargs):
    result = handler(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Bot 500 is owned by user 100. Without a guard the handlers charge the bucket
# to user 100 (the bot's ``user_id``), not to bot 500.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")

_HANDLERS = [
    reactions_routes.add_reaction,
    reactions_routes.clear_reactions_for_emoji,
    reactions_routes.remove_reaction,
    reactions_routes.clear_reactions,
]


def _kwargs(handler, user):
    common = {"request": _Request(), "current_user": user}
    if handler.__name__ == "clear_reactions":
        return {"channel_id": "1", "message_id": "2", **common}
    return {"channel_id": "1", "message_id": "2", "emoji": "x", **common}


@pytest.fixture
def bucket(monkeypatch):
    """Record every limiter principal, then stop the handler at that point."""
    calls = []

    async def _record(**kwargs):
        calls.append(kwargs.get("user_id"))
        raise _ReachedBucket()

    monkeypatch.setattr(ratelimit, "acheck_rate_limit", _record)
    return calls


@pytest.mark.parametrize("handler", _HANDLERS, ids=lambda h: h.__name__)
def test_bot_refused_before_owner_rate_bucket(handler, bucket):
    with pytest.raises(HTTPException) as exc:
        _call(handler, **_kwargs(handler, _BOT))

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    # The owner's per-account reaction bucket was never touched.
    assert bucket == []


@pytest.mark.parametrize("handler", _HANDLERS, ids=lambda h: h.__name__)
def test_user_still_reaches_owner_rate_bucket(handler, bucket):
    """Control: a human session still reaches the per-account rate limiter."""
    with pytest.raises(_ReachedBucket):
        _call(handler, **_kwargs(handler, _USER))

    assert bucket == [SnowflakeID("100")]


@pytest.mark.parametrize("handler", _HANDLERS, ids=lambda h: h.__name__)
def test_handler_source_calls_require_user_token(handler):
    """Static guard so a future edit cannot silently drop the runtime check."""
    assert "require_user_token(current_user)" in inspect.getsource(handler)
