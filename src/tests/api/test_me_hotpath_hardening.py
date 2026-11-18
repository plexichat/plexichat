"""Regression tests for the /users/@me hot read endpoints.

Covers the three cached handlers behind
``GET /api/v1/users/@me/settings``, ``GET /api/v1/users/@me/unread`` and
``GET /api/v1/users/@me/channels``:

1. A warm cache hit must not pay a Valkey round-trip. The @cached decorator
   used Valkey first even when an in-process L1 copy existed, so a remote
   Valkey RTT (~50ms) was charged on *every* request. These handlers now opt
   into a bounded L1-first tier; warm calls must finish well under 20ms.
2. Responses must be identical cold vs warm (the L1 tier must reconstruct the
   same pydantic model as the Valkey/miss paths).
3. The cache key must be partitioned by credential (bot vs owner) and by
   user id, and bot credentials must be rejected on owner-private reads.
4. Invalidation must clear the L1 tier, and the L1 lifetime must be bounded.
"""

import asyncio
import inspect
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.routes.messages.read_receipts import get_all_unread_counts
from src.api.routes.settings import get_all_settings
from src.api.routes.users.composer import UsersRouter
from src.api.routes.users.helpers import authenticated_cache_key
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType
from src.core.database.cache import decorators, operations
from src.core.database.cache.base import _mem_cache
from src.core.database.cache.operations import invalidate_cached
from src.core.database.cache.serialization import generate_cache_key


OWNER_ID = 100
BOT_ID = 500
OTHER_ID = 200


def _token(user_id, *, token_type="user", account_id=None, session_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=SnowflakeID(str(session_id)) if session_id is not None else None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _jsonable(value):
    """Cached (L1/Valkey) reads replay JSON dicts; cold reads return models."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _field(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name)


class SlowValkey:
    """Valkey stand-in whose GET simulates a remote network round-trip."""

    def __init__(self, delay=0.05):
        self.store = {}
        self.delay = delay
        self.gets = 0

    def get_json(self, key):
        self.gets += 1
        time.sleep(self.delay)
        return self.store.get(key)

    def set_json(self, key, value, ttl=None):
        self.store[key] = value

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    def keys(self, pattern):
        import fnmatch

        return [k for k in self.store if fnmatch.fnmatch(k, pattern)]


@pytest.fixture(autouse=True)
def _isolate_cache():
    _mem_cache.clear()
    yield
    _mem_cache.clear()


@pytest.fixture
def slow_valkey():
    client = SlowValkey()
    with (
        patch.object(decorators, "get_client", lambda: client),
        patch.object(decorators, "is_available", lambda: True),
        patch.object(operations, "get_client", lambda: client),
        patch.object(operations, "is_available", lambda: True),
    ):
        yield client


def _settings_module(values):
    return SimpleNamespace(
        get_all_settings=lambda uid: dict(values),
        get_setting_limit=lambda: 100,
    )


def _unread_module():
    return SimpleNamespace(get_unread_count=lambda uid: {5: 2, 7: 1})


def _channel_modules():
    conv = SimpleNamespace(
        id=SnowflakeID("900"),
        recipient_id=SnowflakeID("777"),
        last_message_id=SnowflakeID("888"),
        conversation_type="dm",
    )
    messaging = SimpleNamespace(get_conversations=lambda uid: [conv])
    auth = SimpleNamespace(
        get_user_profiles_bulk=lambda ids: {str(ids[0]): {"username": "bob"}}
    )
    return messaging, auth


def _install(monkeypatch, *, settings=None, messaging=None, auth=None):
    if settings is not None:
        monkeypatch.setattr(api, "get_settings", lambda: settings)
    if messaging is not None:
        monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    if auth is not None:
        monkeypatch.setattr(api, "get_auth", lambda: auth)


def _handlers():
    router = UsersRouter()
    return [
        ("settings", lambda tok: _call(get_all_settings, current_user=tok)),
        ("unread", lambda tok: _call(get_all_unread_counts, current_user=tok)),
        ("channels", lambda tok: _call(router.get_dm_channels, current_user=tok)),
    ]


@pytest.mark.parametrize("name,handler", _handlers())
def test_warm_me_reads_are_fast_and_identical(monkeypatch, slow_valkey, name, handler):
    messaging, auth = _channel_modules()
    _install(
        monkeypatch,
        settings=_settings_module({"theme": "dark"}),
        messaging=_unread_module() if name == "unread" else messaging,
        auth=auth,
    )
    tok = _token(OWNER_ID)

    cold = handler(tok)
    gets_after_cold = slow_valkey.gets
    t0 = time.perf_counter()
    warm = handler(tok)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert _jsonable(warm) == _jsonable(cold)  # identical response from L1
    assert slow_valkey.gets == gets_after_cold  # warm hit did NOT call Valkey
    assert elapsed_ms < 20, f"{name} warm read took {elapsed_ms:.2f}ms"


@pytest.mark.parametrize("name,handler", _handlers())
def test_bot_token_cannot_read_owner_private_data(monkeypatch, name, handler):
    observed = {"settings": 0, "unread": 0, "channels": 0}

    settings = SimpleNamespace(
        get_all_settings=lambda uid: (
            observed.__setitem__("settings", observed["settings"] + 1) or {}
        ),
        get_setting_limit=lambda: 100,
    )
    messaging = SimpleNamespace(
        get_unread_count=lambda uid: (
            observed.__setitem__("unread", observed["unread"] + 1) or {}
        ),
        get_conversations=lambda uid: (
            observed.__setitem__("channels", observed["channels"] + 1) or []
        ),
    )
    auth = SimpleNamespace(get_user_profiles_bulk=lambda ids: {})
    _install(monkeypatch, settings=settings, messaging=messaging, auth=auth)

    bot = _token(OWNER_ID, token_type="bot", account_id=BOT_ID)
    with pytest.raises(HTTPException) as exc:
        handler(bot)

    assert exc.value.status_code == 403
    assert observed[name] == 0  # no private read was performed for the bot


@pytest.mark.parametrize(
    "prefix", ["user_settings_all_api", "unread_counts_api", "user_dm_channels_api"]
)
def test_cache_key_partitioned_by_credential_and_user(prefix):
    builder = authenticated_cache_key(prefix)
    owner = _token(OWNER_ID, session_id=1)
    owner_other_session = _token(OWNER_ID, session_id=2)
    bot = _token(OWNER_ID, token_type="bot", account_id=BOT_ID)
    other = _token(OTHER_ID)

    owner_key = builder(current_user=owner)
    assert owner_key == generate_cache_key(prefix, current_user=owner)
    assert builder(current_user=owner_other_session) == owner_key
    assert builder(current_user=other) != owner_key
    assert f":TokenInfo:{OTHER_ID}" in builder(current_user=other)

    bot_key = builder(current_user=bot)
    assert ":bot:" in bot_key
    assert bot_key != owner_key
    assert bot_key != builder(
        current_user=_token(OWNER_ID, token_type="bot", account_id=501)
    )


def test_credentials_do_not_share_cached_responses(monkeypatch):
    """Two users with different private data must never read each other's L1."""
    settings = SimpleNamespace(
        get_all_settings=lambda uid: {"owner": "A" if int(uid) == OWNER_ID else "B"},
        get_setting_limit=lambda: 100,
    )
    _install(monkeypatch, settings=settings)
    first = _token(OWNER_ID)
    second = _token(OTHER_ID)

    a = _call(get_all_settings, current_user=first)
    b = _call(get_all_settings, current_user=second)
    a_again = _call(get_all_settings, current_user=first)
    b_again = _call(get_all_settings, current_user=second)

    assert _field(a, "settings") == {"owner": "A"}
    assert _field(b, "settings") == {"owner": "B"}
    assert _jsonable(a_again) == _jsonable(a)
    assert _jsonable(b_again) == _jsonable(b)


def test_invalidation_clears_l1_and_l1_lifetime_is_bounded(monkeypatch, slow_valkey):
    """A write invalidation must drop the L1 copy; L1 TTL must stay bounded."""
    payloads = iter([{"v": "old"}, {"v": "new"}])
    settings = SimpleNamespace(
        get_all_settings=lambda uid: next(payloads),
        get_setting_limit=lambda: 100,
    )
    _install(monkeypatch, settings=settings)
    tok = _token(OWNER_ID)

    old = _call(get_all_settings, current_user=tok)
    assert _field(old, "settings") == {"v": "old"}

    key = generate_cache_key("user_settings_all_api", current_user=tok)
    expiry, _ = _mem_cache[key]
    assert expiry - time.time() <= 2.5  # bounded L1 lifetime, not the 60s Valkey TTL

    invalidate_cached(key)  # what a settings write does
    new = _call(get_all_settings, current_user=tok)
    assert _field(new, "settings") == {"v": "new"}


def test_other_cached_routes_keep_valkey_first(slow_valkey):
    """Opt-in L1 must not change the default behaviour of unrelated routes."""

    @decorators.cached(ttl=60, prefix="l1_default_probe")
    def legacy(value):
        legacy.calls += 1
        return value * 2

    legacy.calls = 0
    assert legacy(3) == 6
    gets_after_cold = slow_valkey.gets
    assert legacy(3) == 6
    assert slow_valkey.gets == gets_after_cold + 1  # still a Valkey GET on a warm hit
