"""Account-bound media routes must not let a bot token spend its owner's quota.

A bot token's ``TokenInfo.user_id`` is its human owner (the bot's own id lives
in ``account_id``). Two handlers in ``routes/media.py`` build per-account rate
buckets directly from that id:

* ``report_content_hash`` -> ``media:hash_report:<user_id>``
* ``get_proxied_content`` -> ``media:proxy:user:<user_id>``

Without an identity gate a leaked bot token lands on the owner's bucket: it can
exhaust the owner's hash-report / proxy-fetch quota (and, for hash reports,
drive the hash toward a global auto-block) while evading its own. This is the
same owner-identity confusion already rejected on the report, feedback,
discovery, search, avatar, thread and sticker-creation routes.

The runtime tests below are non-vacuous: before the guards were added the bot
token reached (and recorded) the rate-limit lookup and these tests failed.
Control tests prove a legitimate user token still reaches each limiter.

Run with::

    pytest src/tests/security/test_media_bot_token_owner_quota_guard.py -q -n 0
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes import media as media_routes
from src.api.schemas.common import SnowflakeID
from src.api.schemas.media import HashReportRequest
from src.core.auth.models import AccountType
from src.core.database import cache as db_cache

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


# Bot 500 is owned by user 100. Without a guard the handlers resolve the
# owner's identity and charge the bucket to user 100.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")
_REPORT = HashReportRequest(hash_value="a" * 64, reason="spam")
_PROXY_URL = "https://example.com/image.png"


class _ReachedBucket(BaseException):
    """Sentinel raised by the patched limiter to stop the handler right there.

    Derives from ``BaseException`` so a broad ``except Exception`` cannot turn
    a proven bucket lookup into a generic 500.
    """


@pytest.fixture
def hash_bucket(monkeypatch):
    """Record every hash-report bucket lookup, then stop the handler there."""
    calls = []

    def _record(user_id):
        calls.append(user_id)
        raise _ReachedBucket()

    monkeypatch.setattr(media_routes, "_check_hash_report_rate_limit", _record)
    return calls


@pytest.fixture
def proxy_bucket(monkeypatch):
    """Record every proxy bucket lookup, then stop the handler there."""
    calls = []

    def _record(key, limit, window):
        calls.append(key)
        raise _ReachedBucket()

    # Keep the pre-rate-limit validation and config reads from short-circuiting.
    monkeypatch.setattr(media_routes, "_is_allowed_proxy_url", lambda url: True)
    monkeypatch.setattr(media_routes, "_proxy_rate_limit_params", lambda: (10, 60))
    # The handler imports check_rate_limit locally; patch the source module.
    monkeypatch.setattr(db_cache, "check_rate_limit", _record)
    return calls


def test_bot_refused_before_hash_report_bucket(hash_bucket):
    with pytest.raises(HTTPException) as exc:
        _call(
            media_routes.report_content_hash,
            report=_REPORT,
            request=None,
            user=_BOT,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    # The owner's media:hash_report:<id> bucket was never touched.
    assert hash_bucket == []


def test_user_still_reaches_hash_report_bucket(hash_bucket):
    """Control: a human session still reaches the per-account rate limiter."""
    with pytest.raises(_ReachedBucket):
        _call(
            media_routes.report_content_hash,
            report=_REPORT,
            request=None,
            user=_USER,
        )

    assert hash_bucket == [SnowflakeID("100")]


def test_bot_refused_before_proxy_bucket(proxy_bucket):
    with pytest.raises(HTTPException) as exc:
        _call(media_routes.get_proxied_content, url=_PROXY_URL, user=_BOT)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    # The owner's media:proxy:user:<id> bucket was never touched.
    assert proxy_bucket == []


def test_user_still_reaches_proxy_bucket(proxy_bucket):
    """Control: a human session still reaches the per-account rate limiter."""
    with pytest.raises(_ReachedBucket):
        _call(media_routes.get_proxied_content, url=_PROXY_URL, user=_USER)

    assert proxy_bucket == [f"media:proxy:user:{_USER.user_id}"]


@pytest.mark.parametrize(
    "handler",
    [media_routes.report_content_hash, media_routes.get_proxied_content],
)
def test_handler_source_calls_require_user_token(handler):
    """Static guard so a future edit cannot silently drop the runtime check."""
    assert "require_user_token(user)" in inspect.getsource(handler)
