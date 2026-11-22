"""Pin routes must translate service domain errors into static 400s, not 500s.

``PUT /channels/{channel_id}/pins/{message_id}`` (``pins.pin_message``) calls
``messaging.pin_message``, whose service raises ``PinLimitError`` when the
conversation's pin quota is full (``services/pin.py``). That is a
client-actionable outcome, not a server fault: the core message embeds the
configured quota and current count. The route had no ``except`` arm for it, so
it fell through to ``except Exception -> 500`` and the error log echoed
``str(exc)`` verbatim. ``MessageNotPinnedError`` (raised by the sibling
``unpin_message`` service when the target is already unpinned) had the same
gap. Both are mapped here to 400 with a STATIC body: the raw ``str(exc)``
(carrying internal detail) never reaches the client.

The tests drive the real routes with a wired-enough messaging/servers stub so
the bind-before-mutate channel guards pass and execution actually reaches the
service call. Before the fix each raises 500 (and the test fails); after the
fix each raises 400 with a stable message.
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api_module
from src.api.routes.messages import pins as routes
from src.core.messaging.exceptions import (
    MessageNotPinnedError,
    PinLimitError,
)

CHANNEL_ID = 123456789
MESSAGE_ID = 987654321

pytestmark = pytest.mark.unit


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


class _FakeMessaging:
    """get_message yields a live target in the addressed channel; pin/unpin raise."""

    def __init__(self, exc, unpin_exc=None):
        self._exc = exc
        self._unpin_exc = unpin_exc

    def get_message(self, user_id, message_id):
        return SimpleNamespace(conversation_id=CHANNEL_ID, id=message_id)

    def pin_message(self, user_id, message_id):
        raise self._exc

    def unpin_message(self, user_id, message_id):
        raise self._unpin_exc


class _FakeServers:
    """Wired enough for the channel guards: get_channel grants view.

    Deliberately exposes no ``get_channel_conversation_id`` (scope stays
    ``{channel_id}``) and no ``get_channel_by_conversation`` (reverse-lookup
    guard takes its legacy-stub early return).
    """

    def get_channel(self, channel_id, user_id):
        return SimpleNamespace(id=channel_id, server_id=None)


def test_pin_limit_is_400_not_500_with_static_message(monkeypatch):
    # Internal quota/count detail must never appear in the response body.
    exc = PinLimitError(
        "pin quota INTERNAL_DETAIL max=50 current=50", 50, 50
    )
    monkeypatch.setattr(
        api_module, "get_messaging", lambda: _FakeMessaging(exc)
    )
    monkeypatch.setattr(api_module, "get_servers", lambda: _FakeServers())

    with pytest.raises(HTTPException) as ei:
        _call(
            routes.pin_message,
            channel_id=str(CHANNEL_ID),
            message_id=str(MESSAGE_ID),
            current_user=SimpleNamespace(user_id=100),
        )

    # A quota is a client-actionable outcome, never a 500.
    assert ei.value.status_code == 400
    body = ei.value.detail["error"]
    assert body["code"] == 400
    assert "INTERNAL_DETAIL" not in str(body)
    assert "50" not in body["message"]
    assert body["message"] == "Pin limit reached"


def test_pin_limit_arm_precedes_generic_500(monkeypatch):
    """Any PinLimitError reaching the route is a 400, regardless of payload."""
    exc = PinLimitError("SECRET_PATTERN", 1, 1)
    monkeypatch.setattr(
        api_module, "get_messaging", lambda: _FakeMessaging(exc)
    )
    monkeypatch.setattr(api_module, "get_servers", lambda: _FakeServers())

    with pytest.raises(HTTPException) as ei:
        _call(
            routes.pin_message,
            channel_id=str(CHANNEL_ID),
            message_id=str(MESSAGE_ID),
            current_user=SimpleNamespace(user_id=100),
        )

    assert ei.value.status_code == 400
    assert "SECRET_PATTERN" not in str(ei.value.detail)


def test_unpin_not_pinned_is_400_not_500_with_static_message(monkeypatch):
    """A second messaging domain error must not fall into the generic 500 arm."""
    exc = MessageNotPinnedError("SECRET_UNPIN_DETAIL")
    monkeypatch.setattr(
        api_module,
        "get_messaging",
        lambda: _FakeMessaging(None, unpin_exc=exc),
    )
    monkeypatch.setattr(api_module, "get_servers", lambda: _FakeServers())

    with pytest.raises(HTTPException) as ei:
        _call(
            routes.unpin_message,
            channel_id=str(CHANNEL_ID),
            message_id=str(MESSAGE_ID),
            current_user=SimpleNamespace(user_id=100),
        )

    assert ei.value.status_code == 400
    body = ei.value.detail["error"]
    assert body["code"] == 400
    assert "SECRET_UNPIN_DETAIL" not in str(body)
    assert body["message"] == "Message is not pinned"
