"""``GET /{server_id}/channels`` must not leak a bot owner's channel visibility.

``get_server_channels`` returns the channels the authenticated account may see:
``servers_mod.get_channels(current_user.user_id, sid)`` filters the server's
channels by that id's ``channels.view`` permission. A bot token carries its
HUMAN OWNER's ``user_id`` (the bot's own id lives in ``account_id``), so before
the fix a bot could:

* on a cache miss, pass the membership gate as its owner and read the owner's
  permission-filtered channel list; and
* on a cache hit, replay the owner's warm
  ``server_channels_api:...:TokenInfo:<uid>`` entry via the DEFAULT key builder
  without the handler body (and its guard) running at all.

The route now rejects bot credentials with ``require_user_token`` and partitions
the cache per credential with ``authenticated_cache_key``. The tests below prove
the bot is refused before any backend lookup on a miss, cannot replay a warm
owner entry on a hit, and that a user token still works with a key layout the
existing ``server_channels_api:*`` invalidation glob still matches. Run with::

    pytest src/tests/security/test_server_channels_bot_token_owner_guard.py -q -n 0
"""

import asyncio
import fnmatch
import inspect

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.servers import channels as server_channels
from src.api.routes.users.helpers import authenticated_cache_key
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
# identity and either reads the owner's backend channel list or its warm entry.
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


def _invoke(current_user):
    return _call(
        server_channels.get_server_channels,
        server_id="10",
        current_user=current_user,
        _channel_list_access_gate=None,
    )


def test_bot_token_refused_before_owner_backend_lookup(backend_guarded):
    with pytest.raises(HTTPException) as exc:
        _invoke(_BOT)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_user_token_reaches_owner_backend_lookup(backend_guarded):
    """Control: a human session gets past the gate to the backend.

    Proves the 403 above is specific to bot credentials and that the test is
    non-vacuous -- without the guard the bot would raise the same sentinel.
    """
    with pytest.raises(_ReachedBackend):
        _invoke(_OWNER)


def test_bot_cannot_replay_owner_warm_cache(monkeypatch):
    """A warm owner entry must not serve a bot token on a cache hit."""

    class _Servers:
        def server_exists(self, sid):
            return True

        def get_channels(self, user_id, sid):
            assert int(user_id) == 100
            return []

    monkeypatch.setattr(api, "get_servers", lambda: _Servers())

    # Warm the owner's entry through the real @cached wrapper.
    assert _invoke(_OWNER) == []

    # Same user_id, different credential. Credential partitioning keeps this
    # call off the owner's key so the body-level guard runs and refuses it. If
    # the route had kept the default key builder, this would have been a cache
    # HIT on the owner's entry and returned [] instead of raising.
    with pytest.raises(HTTPException) as exc:
        _invoke(_BOT)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_user_key_matches_invalidation_glob_and_bot_is_partitioned():
    """User keys keep the legacy layout; bots get a distinct namespace."""

    builder = authenticated_cache_key("server_channels_api")
    user_key = builder(
        server_id="10", current_user=_OWNER, _channel_list_access_gate=None
    )
    bot_key = builder(
        server_id="10", current_user=_BOT, _channel_list_access_gate=None
    )

    # The user token keeps the exact prefix the mutation invalidation globs
    # (``invalidate_pattern("server_channels_api:*")``) clear.
    assert user_key.startswith("server_channels_api:")
    assert fnmatch.fnmatch(user_key, "server_channels_api:*")
    # The bot is in its own namespace, so a warm owner entry can never be
    # replayed on a cache hit.
    assert bot_key != user_key
    assert ":bot:500:" in bot_key
