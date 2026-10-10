"""
Regression tests for presence bug fixes.

- P1: expired custom statuses must not surface on read
      (``get_presence`` / ``get_visible_presence``).
- P2: a WebSocket presence update that carries a custom status must persist it
      (previously it was broadcast-only and lost on reconnect).
- P3: ``set_custom_status`` normalises / clears whitespace-only text.
- P4: ``PUT /users/@me/presence`` must not close the shared global DB
      connection (it must stay usable for subsequent requests).
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# P1 / P3 — manager-level
# --------------------------------------------------------------------------- #


def test_expired_custom_status_cleaned_on_read(fresh_users):
    user1, _user2, presence_manager = fresh_users
    past = int(time.time() * 1000) - 10_000
    presence_manager.set_custom_status(
        user_id=user1.id, text="stale", emoji=":x:", expires_at=past
    )
    pres = presence_manager.get_presence(user1.id)
    assert pres.custom_status is None


def test_visible_presence_drops_expired_custom_status(fresh_users):
    user1, user2, presence_manager = fresh_users
    past = int(time.time() * 1000) - 10_000
    presence_manager.set_custom_status(user_id=user1.id, text="stale", expires_at=past)
    vis = presence_manager.get_visible_presence(user2.id, user1.id)
    assert vis.custom_status is None


def test_whitespace_custom_status_is_cleared(fresh_users):
    user1, _user2, presence_manager = fresh_users
    presence_manager.set_custom_status(user_id=user1.id, text="real", emoji=":x:")
    out = presence_manager.set_custom_status(user_id=user1.id, text="   ")
    assert out.custom_status is None
    assert presence_manager.get_presence(user1.id).custom_status is None


def test_custom_status_text_is_trimmed(fresh_users):
    user1, _user2, presence_manager = fresh_users
    out = presence_manager.set_custom_status(user_id=user1.id, text="  hello  ")
    assert out.custom_status.text == "hello"


# --------------------------------------------------------------------------- #
# P2 — WebSocket handler persists custom status
# --------------------------------------------------------------------------- #


def test_ws_presence_update_persists_custom_status(fresh_users):
    user1, _user2, presence_manager = fresh_users
    from src.api.websocket.handlers.presence import PresenceHandler

    class FakeConn:
        user_id = user1.id
        is_authenticated = True

    handler = PresenceHandler(presence_manager)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            handler.handle_presence_update(
                FakeConn(),
                {
                    "status": "online",
                    "custom_status": "via ws",
                    "custom_emoji": ":x:",
                },
            )
        )
    finally:
        loop.close()

    pres = presence_manager.get_presence(user1.id)
    assert pres.custom_status is not None
    assert pres.custom_status.text == "via ws"


# --------------------------------------------------------------------------- #
# P4 — route must not close the shared global DB connection
# --------------------------------------------------------------------------- #


def test_update_presence_does_not_close_shared_db(test_client, auth_manager):
    import src.api as api
    from src.utils import encryption
    from unittest.mock import patch

    unique = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=f"pres_{unique}",
            email=f"{unique}@test.local",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(f"pres_{unique}", "TestPass123!")

    token = result.token
    headers = {"Authorization": f"Bearer {token}"}

    resp = test_client.put(
        "/api/v1/users/@me/presence",
        json={"status": "online", "custom_status": "hi", "custom_emoji": ":x:"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    # The shared connection must still be usable: a follow-up DB-backed request
    # must not 500 (it would if the route had closed api.get_db()).
    following = test_client.get(f"/api/v1/users/{user.id}/presence", headers=headers)
    assert following.status_code == 200, following.text

    # And the connection object is still open (not closed by the route).
    shared_db = api.get_db()
    assert shared_db is not None
    # A trivial query must succeed.
    shared_db.execute("SELECT 1")
