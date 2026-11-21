"""A failed notification-settings lookup must FAIL CLOSED on device push.

``send_push``/``send_push_async`` use ``get_notification_settings(...).mobile_push``
as the authorization gate for delivering a push to a user's devices. The gate
used to swallow *any* error from that lookup and fall through to delivery
("settings lookup failed, delivering anyway"), so a user whose ``mobile_push``
opt-out could not be resolved -- backend hiccup, missing/corrupt cache entry,
or an uninitialized notifications module -- still received the device push.

These tests pin that an unavailable preference store suppresses delivery, while
a control proves the fake transport really would deliver when the gate allows.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.core.push.manager import PushManager


class _StubDB:
    """Minimal DB that satisfies PushManager construction; no real writes."""

    def execute(self, *args, **kwargs):
        return None

    def fetch_all(self, *args, **kwargs):
        return []

    def fetch_one(self, *args, **kwargs):
        return None

    def column_exists(self, *args, **kwargs):
        return True


class _SettingsStub:
    def __init__(self, mobile_push=True, raises=False):
        self.mobile_push = mobile_push
        self.raises = raises
        self.calls = []

    def get_notification_settings(self, user_id, server_id=None):
        self.calls.append((user_id, server_id))
        if self.raises:
            raise RuntimeError("notification settings backend unavailable")
        return SimpleNamespace(mobile_push=self.mobile_push)


@pytest.fixture
def push_env():
    settings = _SettingsStub()
    mgr = PushManager(_StubDB(), notifications_module=settings)
    delivered = []

    # One registered device. The transport is faked so a successful push is
    # observable, and the control below proves it really fires.
    mgr.get_user_tokens = lambda user_id, platform=None: [
        {"token": "device-token", "platform": "web"}
    ]
    mgr._send_to_platform = lambda rec, title, body, data, badge, sound, priority: (
        delivered.append(rec["token"]) or True
    )
    return mgr, settings, delivered


def test_settings_lookup_error_suppresses_push_sync(push_env):
    mgr, settings, delivered = push_env
    payload = {"server_id": "123"}

    # Control: with a working settings gate, the fake transport delivers.
    assert mgr.send_push(7, "t", "b", data=payload) == 1
    assert delivered == ["device-token"]
    assert settings.calls == [(7, 123)]

    # Now the preference store fails. The opt-out can no longer be read, so
    # delivery must be suppressed rather than silently granted.
    delivered.clear()
    settings.raises = True
    assert mgr.send_push(7, "t", "b", data=payload) == 0
    assert delivered == []
    assert settings.calls[-1] == (7, 123)


def test_settings_lookup_error_suppresses_push_async(push_env):
    mgr, settings, delivered = push_env
    payload = {"server_id": "123"}

    # Control: the async gate also resolves server scope and delivers.
    assert asyncio.run(mgr.send_push_async(7, "t", "b", data=payload)) == 1
    assert delivered == ["device-token"]
    assert settings.calls == [(7, 123)]

    delivered.clear()
    settings.raises = True
    assert asyncio.run(mgr.send_push_async(7, "t", "b", data=payload)) == 0
    assert delivered == []
    assert settings.calls[-1] == (7, 123)


def test_explicit_opt_out_still_suppresses_push(push_env):
    mgr, settings, delivered = push_env
    settings.mobile_push = False
    assert mgr.send_push(7, "t", "b", data={"server_id": "123"}) == 0
    assert delivered == []
