"""Route tests for DELETE /users/@me/notifications/{id} (F13).

The manager has always implemented ``delete_notification`` with ownership and
unread-count bookkeeping, but no HTTP endpoint exposed it. These tests pin
the route's status-code contract: 400 on a malformed id, 404 when the
notification does not exist (or belongs to someone else), 200 on success.
"""

import asyncio

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.notifications import delete_notification
from src.core.notifications.exceptions import NotificationNotFoundError


class _User:
    def __init__(self, user_id):
        self.user_id = user_id


def _patch(monkeypatch, notif_mod):
    monkeypatch.setattr(api, "get_notifications", lambda: notif_mod)


def test_delete_notification_success(monkeypatch):
    calls = []

    class _Mod:
        def delete_notification(self, user_id, notification_id):
            calls.append((user_id, notification_id))

    _patch(monkeypatch, _Mod())
    result = asyncio.run(delete_notification("123", _User(7)))

    assert result.success is True
    assert calls == [(7, 123)]


def test_delete_notification_deletes_own_row_only(monkeypatch):
    """The manager's ownership check must be reached with the caller's id."""

    class _Mod:
        def delete_notification(self, user_id, notification_id):
            assert user_id == 7
            raise NotificationNotFoundError("Notification not found")

    _patch(monkeypatch, _Mod())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(delete_notification("999", _User(7)))
    assert exc.value.status_code == 404


def test_delete_notification_malformed_id_is_400():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(delete_notification("not-a-number", _User(7)))
    assert exc.value.status_code == 400


def test_delete_notification_missing_module_is_500(monkeypatch):
    _patch(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(delete_notification("123", _User(7)))
    assert exc.value.status_code == 500
