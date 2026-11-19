"""Regression: a bot credential must never act on its owner's account settings.

A bot token's ``user_id`` is the id of its *owner*. The per-user settings
handlers act on ``current_user.user_id``, so without an explicit user-token
guard a bot can read, mutate, bulk-overwrite and delete the account owner's
private settings. ``get_all_settings`` already carried that guard; these four
sibling handlers did not. The cached single-key read additionally needs its
cache partitioned by credential, otherwise a warm entry populated by the
owner's session is served to the bot without the handler body (and its guard)
ever running.
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.routes.settings import (
    bulk_update_settings,
    delete_setting,
    get_setting,
    set_setting,
)
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType
from src.core.database.cache.base import _mem_cache


OWNER_ID = 100
BOT_ID = 500


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    return asyncio.run(result) if inspect.isawaitable(result) else result


class _SettingsModule:
    """Settings backend that records every access with the acting user id."""

    def __init__(self):
        self.calls = []

    def get_config(self):
        return SimpleNamespace(max_bulk_entries=100, max_bulk_payload_bytes=100000)

    def get_one(self, uid, key):
        self.calls.append(("get_one", int(uid), key))
        return SimpleNamespace(key=key, value="secret", created_at=0, updated_at=0)

    def set_setting(self, uid, key, value):
        self.calls.append(("set_setting", int(uid), key))
        return SimpleNamespace(key=key, value=value, created_at=0, updated_at=0)

    def set_many(self, uid, items):
        self.calls.append(("set_many", int(uid)))
        return list(items.keys())

    def delete_setting(self, uid, key):
        self.calls.append(("delete_setting", int(uid), key))
        return True


@pytest.fixture(autouse=True)
def _isolate_cache():
    _mem_cache.clear()
    yield
    _mem_cache.clear()


def _install(monkeypatch, module):
    monkeypatch.setattr(api, "get_settings", lambda: module)
    monkeypatch.setattr(api, "get_presence", lambda: None)


_HANDLERS = [
    ("get_setting", lambda tok: _call(get_setting, key="theme", current_user=tok)),
    (
        "set_setting",
        lambda tok: _call(
            set_setting,
            key="theme",
            body=SimpleNamespace(value="evil"),
            current_user=tok,
        ),
    ),
    (
        "delete_setting",
        lambda tok: _call(delete_setting, key="theme", current_user=tok),
    ),
    (
        "bulk_update_settings",
        lambda tok: _call(
            bulk_update_settings, body={"theme": "evil"}, current_user=tok
        ),
    ),
]


@pytest.mark.parametrize("name,handler", _HANDLERS)
def test_bot_token_cannot_touch_owner_settings(monkeypatch, name, handler):
    module = _SettingsModule()
    _install(monkeypatch, module)
    bot = _token(OWNER_ID, token_type="bot", account_id=BOT_ID)

    with pytest.raises(HTTPException) as exc:
        handler(bot)

    assert exc.value.status_code == 403
    assert module.calls == [], "settings backend was touched with the bot credential"


def test_cached_setting_read_rejects_bot_even_after_owner_warms_cache(monkeypatch):
    """A warm owner cache entry must not authorize a bot credential."""
    module = _SettingsModule()
    _install(monkeypatch, module)
    owner = _token(OWNER_ID)
    bot = _token(OWNER_ID, token_type="bot", account_id=BOT_ID)

    resp = _call(get_setting, key="theme", current_user=owner)
    assert resp.value == "secret"
    assert module.calls == [("get_one", OWNER_ID, "theme")]

    with pytest.raises(HTTPException) as exc:
        _call(get_setting, key="theme", current_user=bot)
    assert exc.value.status_code == 403
    assert module.calls == [("get_one", OWNER_ID, "theme")]


@pytest.mark.parametrize("name,handler", _HANDLERS)
def test_owner_token_still_works(monkeypatch, name, handler):
    module = _SettingsModule()
    _install(monkeypatch, module)
    resp = handler(_token(OWNER_ID))
    assert resp is not None
    assert module.calls, f"{name} did not reach the settings backend for the owner"
