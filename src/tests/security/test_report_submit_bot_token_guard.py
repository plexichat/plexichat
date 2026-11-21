"""Report submission must not let a bot token spend its owner's quota.

A bot token's ``TokenInfo.user_id`` is its human owner (the bot's own id lives
in ``account_id``). Both report-submission handlers in ``routes/reports.py``
use ``current_user.user_id`` as the per-account rate-limit bucket key
(``reports:submit:<id>``), so with no identity gate a bot credential lands on
the owner's bucket: a leaked bot token can exhaust the owner's report quota
and a bot can evade its own. The enhanced-report sibling in
``features/reports.py`` already calls ``require_user_token``; these two were
the remaining gaps.

The runtime tests below are non-vacuous: before the guard was added the bot
token reached (and recorded) the rate-limit lookup and these tests failed.

Run with::

    pytest src/tests/security/test_report_submit_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.routes import reports as report_routes
from src.api.schemas.common import SnowflakeID
from src.api.schemas.reports import MessageReportRequest, UserReportRequest
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


# Bot 500 is owned by user 100. Without a guard the handlers resolve the
# owner's identity and charge the report to user 100's bucket.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")
_MSG_BODY = MessageReportRequest(
    message_id=SnowflakeID("900"), channel_id=SnowflakeID("901"), reason="spam"
)
_USER_BODY = UserReportRequest(user_id=SnowflakeID("902"), reason="spam")


class _ReachedBucket(Exception):
    """Sentinel raised by the patched rate limiter to stop the handler here."""


@pytest.fixture
def bucket(monkeypatch):
    """Record every bucket lookup, then stop the handler right there."""
    calls = []

    def _record(user_id):
        calls.append(user_id)
        raise _ReachedBucket()

    monkeypatch.setattr(report_routes, "_check_report_rate_limit", _record)
    # Keep the pre-rate-limit module-availability checks from short-circuiting.
    monkeypatch.setattr(api, "get_reports", lambda: object())
    monkeypatch.setattr(api, "get_messaging", lambda: object())
    return calls


_CASES = [
    (report_routes.report_message, _MSG_BODY),
    (report_routes.report_user, _USER_BODY),
]


@pytest.mark.parametrize("handler,body", _CASES)
def test_bot_token_refused_before_report_bucket(handler, body, bucket):
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_BOT, body=body)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    # The owner's reports:submit:<id> bucket was never touched.
    assert bucket == []


@pytest.mark.parametrize("handler,body", _CASES)
def test_user_token_still_reaches_report_bucket(handler, body, bucket):
    """Control: a human session still reaches the per-account rate limiter."""
    with pytest.raises(_ReachedBucket):
        _call(handler, current_user=_USER, body=body)

    assert bucket == [SnowflakeID("100")]


@pytest.mark.parametrize("handler", [case[0] for case in _CASES])
def test_handler_source_calls_require_user_token(handler):
    """Static guard so a future edit cannot silently drop the runtime check."""
    assert "require_user_token(current_user)" in inspect.getsource(handler)
