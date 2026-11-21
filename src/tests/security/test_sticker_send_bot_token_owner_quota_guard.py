"""Sticker usage tracking must not let a bot token spend its owner's quota.

``send_sticker_usage`` in ``routes/stickers.py`` charges the per-account rate
bucket directly from ``current_user.user_id``:

* ``POST /stickers/send`` -> ``acheck_rate_limit(user_id=current_user.user_id)``

A bot token's ``TokenInfo.user_id`` is its human *owner* (the bot's own id lives
in ``account_id``). With no identity gate ahead of that call, a leaked bot
credential is charged to the owner's ``user:<owner>:route:POST /stickers/send``
bucket: it drains the victim's sticker quota and evades the bot's own before any
permission gate can reject, and records usage as the owner.

The earlier sticker fix (6f5e6375) gated only ``create_sticker`` and missed this
second manual limiter call. This is the same owner-identity confusion already
rejected on the reaction, media, sticker-create, report, feedback, search and
discovery rate-limited routes.

The runtime tests below are non-vacuous: before the guard was added the bot
token reached (and was recorded by) the patched limiter and these tests failed.
A control test proves a genuine user token still reaches the limiter. A static
test pins that the gate precedes the limiter so a future edit cannot reintroduce
the ordering bug.

Run with::

    pytest src/tests/security/test_sticker_send_bot_token_owner_quota_guard.py -q -n 0
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes import stickers as stickers_routes
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
    """Minimal stand-in: the handler reads ``request.state.is_internal``."""

    def __init__(self):
        self.state = SimpleNamespace()


class _ReachedBucket(BaseException):
    """Sentinel raised by the patched limiter to stop the handler right there.

    Derives from ``BaseException`` so a broad ``except Exception`` in the handler
    cannot turn a proven bucket lookup into a generic 500.
    """


def _call(handler, **kwargs):
    result = handler(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Bot 500 is owned by user 100. Without a guard the handler charges the bucket
# to user 100 (the bot's ``user_id``), not to bot 500.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")

_HANDLER = stickers_routes.send_sticker_usage


def _kwargs(user):
    return {
        "request": _Request(),
        "sticker_id": "1",
        "message_id": "2",
        "current_user": user,
    }


@pytest.fixture
def bucket(monkeypatch):
    """Record every limiter principal, then stop the handler at that point."""
    calls = []

    async def _record(**kwargs):
        calls.append(kwargs.get("user_id"))
        raise _ReachedBucket()

    monkeypatch.setattr(ratelimit, "acheck_rate_limit", _record)
    return calls


def test_bot_refused_before_owner_rate_bucket(bucket):
    with pytest.raises(HTTPException) as exc:
        _call(_HANDLER, **_kwargs(_BOT))

    assert exc.value.status_code == 403
    # The owner's per-account sticker-send bucket was never touched.
    assert bucket == []


def test_user_still_reaches_owner_rate_bucket(bucket):
    """Control: a human session still reaches the per-account rate limiter."""
    with pytest.raises(_ReachedBucket):
        _call(_HANDLER, **_kwargs(_USER))

    assert bucket == [SnowflakeID("100")]


def test_handler_source_gates_before_limiter():
    """Static guard: the identity check must precede the rate-limit spend."""
    source = inspect.getsource(_HANDLER)
    gate = source.index("require_user_token(current_user)")
    spend = source.index("acheck_rate_limit(")
    assert gate < spend, "bot gate must run before the owner-scoped limiter"
