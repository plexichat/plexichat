"""The ``/@me/threads`` handler must not be driven by a bot token.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id is
in ``account_id``). ``GET /users/@me/threads`` is account-bound: it returns the
threads belonging to ``current_user.user_id``. Without an explicit
``require_user_token`` gate, a narrowly-scoped bot credential would silently
read its owner's private thread list.

The first test is non-vacuous: the backend factory is monkeypatched to explode,
so without the fix the handler would call it and raise AssertionError instead
of the expected 403. Run with::

    pytest src/tests/security/test_threads_me_route_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes import threads as threads_routes
from src.api.routes.users.helpers import require_user_token
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


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Bot 500 is owned by user 100; the handler resolves threads for user 100.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100)


def test_bot_token_refused_before_any_thread_backend_lookup(monkeypatch):
    def _boom():
        raise AssertionError("thread backend reached with a bot token")

    monkeypatch.setattr(threads_routes, "_get_threads", _boom)

    with pytest.raises(HTTPException) as exc:
        _call(threads_routes.list_my_threads, current_user=_BOT)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    assert "user token" in exc.value.detail["error"]["message"].lower()


def test_user_token_passes_the_identity_gate(monkeypatch):
    threads_mod = Mock()
    threads_mod.get_user_threads.return_value = []
    monkeypatch.setattr(threads_routes, "_get_threads", lambda: threads_mod)

    result = _call(threads_routes.list_my_threads, current_user=_USER)

    assert result == []
    threads_mod.get_user_threads.assert_called_once_with(_USER.user_id)


def test_guard_helper_accepts_a_real_user_token():
    assert require_user_token(_USER) is None


def test_handler_source_calls_require_user_token():
    routes_file = Path(threads_routes.__file__)
    assert "require_user_token(current_user)" in routes_file.read_text(encoding="utf-8")
