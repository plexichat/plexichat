"""``GET /servers/`` must not let a bot token read its owner's private list.

``get_servers`` is account-bound: it returns the servers the authenticated
account joined and caches that response under a key derived from
``current_user.user_id``. A bot token carries its OWNER's ``user_id`` (the
bot's own id lives in ``account_id``), so before the fix a bot could:

* on a cache miss, have ``servers_mod.get_servers(current_user.user_id)``
  return the owner's joined-server list; and
* on a cache hit, replay the owner's warm
  ``user_servers_api:current_user:TokenInfo:<uid>`` entry without the handler
  body running at all.

The route now rejects bot credentials with ``require_user_token`` and
partitions the cache per credential with ``authenticated_cache_key``. The
tests below prove the rejection happens before any backend lookup and cannot
be bypassed by a warm owner entry (a body-only guard would be skipped on a
cache hit). Run with::

    pytest src/tests/security/test_server_list_bot_token_owner_guard.py -q -n 0
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.servers import server_crud
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType, TokenInfo
from src.core.database.cache import decorators
from src.core.database.cache.base import _mem_cache

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


# Bot 500 is owned by user 100. Without the guard the bot resolves the owner's
# identity and either reads the owner's backend list or its warm cache entry.
_BOT = _token(100, token_type="bot", account_id=500)
_OWNER = _token(100)


class _ReachedBackend(BaseException):
    """Sentinel proving a post-gate backend lookup was reached.

    Derives from ``BaseException`` so the cache wrapper's broad ``except``
    clauses cannot turn a proven lookup into a generic error.
    """


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    """Force the process-local, offline cache tier and reset its state."""

    def _reset():
        _mem_cache.clear()
        decorators.reset_flight_state_internal()

    _reset()
    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)
    yield
    _reset()


@pytest.fixture
def backend_guarded(monkeypatch):
    """Make any post-gate servers-module lookup raise the sentinel."""

    def _unexpected(*args, **kwargs):
        raise _ReachedBackend()

    monkeypatch.setattr(api, "get_servers", _unexpected)
    return _unexpected


def test_bot_token_refused_before_owner_backend_lookup(backend_guarded):
    with pytest.raises(HTTPException) as exc:
        _call(server_crud.get_servers, current_user=_BOT)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_user_token_reaches_owner_backend_lookup(backend_guarded):
    """Control: a human session gets past the gate to the backend.

    This proves the 403 above is specific to bot credentials and that the
    runtime test is non-vacuous -- without the guard the bot would raise the
    same sentinel instead of being refused.
    """
    with pytest.raises(_ReachedBackend):
        _call(server_crud.get_servers, current_user=_OWNER)


def test_bot_cannot_replay_owner_warm_cache(monkeypatch):
    """A warm owner entry must not serve a bot token on a cache hit."""

    class _Servers:
        def get_servers(self, user_id):
            assert int(user_id) == 100
            return []

    monkeypatch.setattr(api, "get_servers", lambda: _Servers())

    # Warm the owner's entry through the real @cached wrapper.
    assert _call(server_crud.get_servers, current_user=_OWNER) == []

    # Same user_id, different credential. Credential partitioning keeps this
    # call off the owner's key so the body-level guard runs and refuses it.
    with pytest.raises(HTTPException) as exc:
        _call(server_crud.get_servers, current_user=_BOT)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
