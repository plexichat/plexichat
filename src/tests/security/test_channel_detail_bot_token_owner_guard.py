"""``_load_channel`` must not let a bot token read its owner's channel detail.

``ChannelCRUDMixin._load_channel`` is the ``@cached`` cold path for the channel
GET. Its response is permission-filtered per ``current_user.user_id``: both
``servers_mod.get_channel(cid, current_user.user_id)`` and
``self._channel_to_response(channel, current_user.user_id)`` are viewer-scoped.
A bot token carries its human OWNER's ``user_id`` (the bot's own id lives in
``account_id``), so before the fix a bot could:

* on a cache miss, pass ``_require_channel_access`` (which only checks the
  owner's membership) and read the owner's filtered channel detail; and
* on a cache hit, replay the owner's warm
  ``channel_api:...:TokenInfo:<uid>`` entry via the DEFAULT key builder without
  the handler body running at all.

The route now rejects bot credentials with ``require_user_token`` and partitions
the cache per credential with ``authenticated_cache_key`` (the key builder still
uses the ``channel_api`` prefix so ``channel_api:*`` invalidation globs match).
Run with::

    pytest src/tests/security/test_channel_detail_bot_token_owner_guard.py -q -n 0
"""

import asyncio
import fnmatch
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.channels.crud import ChannelCRUDMixin
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


def _call(method, *args):
    result = method(*args)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Bot 500 is owned by user 100. Without the guard the bot resolves the owner's
# identity and either reads the owner's backend channel or its warm cache entry.
_BOT = _token(100, token_type="bot", account_id=500)
_OWNER = _token(100)


class _Self:
    """Minimal bound-instance stand-in (the cached helper is a method)."""

    def _channel_to_response(self, channel, user_id):
        return {"id": str(channel.id), "viewer": int(user_id)}


_SELF = _Self()


class _ReachedBackend(BaseException):
    """Sentinel proving a post-gate backend lookup was reached."""


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    def _reset():
        _mem_cache.clear()
        decorators.reset_flight_state_internal()

    _reset()
    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)
    monkeypatch.setattr(api, "get_db", lambda: None)
    yield
    _reset()


@pytest.fixture
def backend_guarded(monkeypatch):
    def _unexpected(*args, **kwargs):
        raise _ReachedBackend()

    monkeypatch.setattr(api, "get_servers", _unexpected)
    return _unexpected


def _invoke(current_user):
    return _call(ChannelCRUDMixin._load_channel, _SELF, "10", current_user)


def _key_builder_of(fn):
    names = fn.__code__.co_freevars
    if "key_builder" not in names or fn.__closure__ is None:
        return None
    return fn.__closure__[names.index("key_builder")].cell_contents


def test_bot_token_refused_before_owner_backend_lookup(backend_guarded):
    with pytest.raises(HTTPException) as exc:
        _invoke(_BOT)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_user_token_reaches_owner_backend_lookup(backend_guarded):
    """Control: a human session gets past the gate to the backend.

    Proves the 403 above is specific to bots and that the test is non-vacuous.
    """
    with pytest.raises(_ReachedBackend):
        _invoke(_OWNER)


def test_bot_cannot_replay_owner_warm_cache(monkeypatch):
    """A warm owner entry must not serve a bot token on a cache hit."""

    class _Servers:
        def channel_exists(self, cid):
            return True

        def get_channel(self, cid, user_id):
            assert int(user_id) == 100
            return SimpleNamespace(id=777)

    monkeypatch.setattr(api, "get_servers", lambda: _Servers())

    # Warm the owner's entry through the real @cached wrapper.
    assert _invoke(_OWNER) == {"id": "777", "viewer": 100}

    # Same user_id, different credential. Credential partitioning keeps this
    # off the owner's key so the body-level guard runs and refuses it. With the
    # default key builder this would have been a cache HIT on the owner's entry
    # and returned the owner's channel dict instead of raising.
    with pytest.raises(HTTPException) as exc:
        _invoke(_BOT)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_user_key_matches_invalidation_glob_and_bot_is_partitioned():
    """The route actually uses a `channel_api` builder that partitions bots."""

    builder = _key_builder_of(ChannelCRUDMixin._load_channel)
    assert builder is not None, "cached helper reverted to the default key builder"

    user_key = builder(_SELF, "10", _OWNER)
    bot_key = builder(_SELF, "10", _BOT)

    # User tokens keep the exact prefix the mutation invalidation globs clear
    # (invalidate_pattern("channel_api:*")).
    assert user_key.startswith("channel_api:")
    assert fnmatch.fnmatch(user_key, "channel_api:*")
    # Bots get a distinct namespace, so a warm owner entry can never be served
    # to one on a cache hit.
    assert bot_key != user_key
    assert ":bot:500:" in bot_key
