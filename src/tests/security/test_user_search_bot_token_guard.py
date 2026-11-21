"""User discovery must not let a bot token spend its owner's quota.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id is
in ``account_id``).  ``search_user_by_username`` uses ``current_user.user_id``
as the per-account rate-limit bucket key, so with no identity gate a bot
credential lands on the owner's ``user_discovery:<id>`` bucket: a leaked bot
token can exhaust the owner's lookup quota, and a bot can evade its own.
Every other user-router handler keyed on ``user_id`` rejects bot credentials;
this one was the remaining gap.

The runtime tests below are non-vacuous: before the guard was added, the bot
token reached (and recorded) the rate-limit lookup and the tests failed.

Run with::

    pytest src/tests/security/test_user_search_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.routes.users import discovery as discovery_routes
from src.api.routes.users.discovery import DiscoveryMixin
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


def _call(**kwargs):
    result = DiscoveryMixin().search_user_by_username(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Bot 500 is owned by user 100. Without the guard the handler resolves the
# owner's identity and charges the lookup to user 100's bucket.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")
_KWARGS = {"username": "someone"}


@pytest.fixture
def rate_limit_recorder(monkeypatch):
    """Record rate-limit calls and fail loudly if the bot reaches the bucket."""

    calls = []

    def _record(user_id):
        calls.append(user_id)

    monkeypatch.setattr(discovery_routes, "_check_discovery_rate_limit", _record)
    return calls


def test_bot_token_refused_before_rate_limit_lookup(rate_limit_recorder):
    with pytest.raises(HTTPException) as exc:
        _call(current_user=_BOT, **_KWARGS)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    # The owner's discovery bucket was never touched.
    assert rate_limit_recorder == []


def test_user_token_passes_the_identity_gate(rate_limit_recorder, monkeypatch):
    """Control: a human session still reaches the per-account rate limiter."""
    auth = Mock()
    auth.get_user_by_username.return_value = None  # -> 404, no backend setup
    monkeypatch.setattr(api, "get_auth", lambda: auth)

    with pytest.raises(HTTPException) as exc:
        _call(current_user=_USER, **_KWARGS)

    assert exc.value.status_code == 404
    assert rate_limit_recorder == [SnowflakeID("100")]


def test_handler_source_calls_require_user_token():
    """Static guard so a future edit cannot silently drop the runtime check."""
    body = inspect.getsource(DiscoveryMixin().search_user_by_username)
    assert "require_user_token(current_user)" in body
