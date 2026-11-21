"""Sticker creation must not let a bot token spend its owner's quota.

A bot token's ``TokenInfo.user_id`` is its human owner (the bot's own id lives
in ``account_id``). ``create_sticker`` in ``routes/stickers.py`` charges its
per-account bucket ``stickers:create:minute:<id>`` from ``current_user.user_id``
and creates the sticker *as* that id. With no identity gate a leaked bot token
lands on the owner's bucket: it can exhaust the owner's sticker-creation quota
(and write stickers under the owner's identity) while evading its own. Every
other owner-scoped, rate-limited route (report, feedback, discovery, search,
avatar, threads, friend requests) already rejects bot credentials this way.

The runtime tests below are non-vacuous: before the guard was added the bot
token reached (and recorded) the rate-limit lookup and these tests failed.
The control test proves a legitimate user token still reaches the limiter.

Run with::

    pytest src/tests/security/test_sticker_create_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.routes import stickers as sticker_routes
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
        permissions={},
        rate_limit_tier="bot" if token_type == "bot" else "standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _call(handler, **kwargs):
    result = handler(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Bot 500 is owned by user 100. Without a guard the handler resolves the
# owner's identity and charges sticker creation to user 100's bucket.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")
_KWARGS = {"server_id": "1", "name": "tag", "image": object()}


class _ReachedBucket(BaseException):
    """Sentinel raised by the patched limiter to stop the handler right there.

    Derives from ``BaseException`` so a broad ``except Exception`` cannot turn
    a proven bucket lookup into a generic 500.
    """


@pytest.fixture
def bucket(monkeypatch):
    """Record every bucket lookup, then stop the handler right there."""
    calls = []

    async def _record(user_id):
        calls.append(user_id)
        raise _ReachedBucket()

    monkeypatch.setattr(sticker_routes, "_check_sticker_create_rate_limit", _record)
    # Keep the pre-rate-limit module-availability check from short-circuiting.
    monkeypatch.setattr(api, "get_stickers", lambda: object())
    return calls


def test_bot_token_refused_before_sticker_create_bucket(bucket):
    with pytest.raises(HTTPException) as exc:
        _call(sticker_routes.create_sticker, current_user=_BOT, **_KWARGS)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    # The owner's stickers:create:minute:<id> bucket was never touched.
    assert bucket == []


def test_user_token_still_reaches_sticker_create_bucket(bucket):
    """Control: a human session still reaches the per-account rate limiter."""
    with pytest.raises(_ReachedBucket):
        _call(sticker_routes.create_sticker, current_user=_USER, **_KWARGS)

    assert bucket == [SnowflakeID("100")]


def test_handler_source_calls_require_user_token():
    """Static guard so a future edit cannot silently drop the runtime check."""
    assert "require_user_token(current_user)" in inspect.getsource(
        sticker_routes.create_sticker
    )
