"""Typing-start username resolution is memoized across a burst.

``handle_typing_start`` used to call ``auth.get_user`` on *every* TYPING_START
frame purely to read ``.username``. That is a threadpool hop plus a full
``User`` rebuild (field decryption + permission parsing) for one string, paid
once per keystroke pause. The REST typing route already avoids this by reading
the username off the verified token; the websocket path had no equivalent.

The username is stable for minutes, so a bounded, short-TTL per-user memo
collapses a burst into a single lookup. The tests pin both the counter (the
actual optimization) and the unchanged event payload (correctness).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import src.api as api
import src.api.websocket as ws
import src.api.websocket.handlers.presence as hp
from src.api.websocket.connection import Connection, ConnectionState

USER = 42
OTHER = 99
CHANNEL = 777
SERVER = 555
VIEWERS = [USER, 43, OTHER]


class _Auth:
    """Counting auth boundary: each get_user is the work we want to avoid."""

    def __init__(self):
        self.calls = 0

    def get_user(self, user_id):
        self.calls += 1
        return SimpleNamespace(username=f"user-{user_id}")


class _Presence:
    def start_typing(self, user_id, channel_id):
        return None


def _wire(monkeypatch, auth):
    """Install counting fakes for every boundary the typing handler touches."""
    servers = SimpleNamespace(
        get_channel=lambda cid, uid: SimpleNamespace(server_id=SERVER),
        get_member_user_ids=lambda sid, exclude_user_id=None: list(VIEWERS),
        has_permission=lambda uid, sid, perm, cid: uid in VIEWERS,
    )
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_auth", lambda: auth)

    dispatched = []

    class Dispatcher:
        async def dispatch_event(self, event, user_ids):
            dispatched.append(
                (event.event_type.value, dict(event.data), list(user_ids))
            )
            return len(user_ids)

    monkeypatch.setattr(ws, "is_setup", lambda: True)
    monkeypatch.setattr(ws, "get_dispatcher", lambda: Dispatcher())

    hp._typing_audience_cache.clear()
    cache = getattr(hp, "_typing_username_cache", None)
    if cache is not None:
        cache.clear()
    return dispatched


def _conn(user_id):
    conn = Connection(websocket=Mock(), connection_id=f"c-{user_id}")
    conn.user_id = user_id
    conn.state = ConnectionState.READY
    return conn


def test_burst_resolves_username_once(monkeypatch):
    auth = _Auth()
    dispatched = _wire(monkeypatch, auth)
    handler = hp.PresenceHandler(presence_module=_Presence())
    conn = _conn(USER)

    for _ in range(8):
        asyncio.run(handler.handle_typing_start(conn, {"channel_id": str(CHANNEL)}))

    # Before the fix this was 8 (one full User rebuild per frame).
    assert auth.calls == 1, f"expected 1 username lookup across burst, got {auth.calls}"
    typing = [d for t, d, _ in dispatched if t == "TYPING_START"]
    assert len(typing) == 8
    assert all(d["username"] == f"user-{USER}" for d in typing)


def test_username_cache_is_per_user(monkeypatch):
    auth = _Auth()
    dispatched = _wire(monkeypatch, auth)
    handler = hp.PresenceHandler(presence_module=_Presence())

    for uid in (USER, OTHER, USER, OTHER):
        asyncio.run(
            handler.handle_typing_start(_conn(uid), {"channel_id": str(CHANNEL)})
        )

    assert auth.calls == 2, f"expected one lookup per distinct user, got {auth.calls}"
    names = {
        d["user_id"]: d["username"] for t, d, _ in dispatched if t == "TYPING_START"
    }
    assert names[str(USER)] == f"user-{USER}"
    assert names[str(OTHER)] == f"user-{OTHER}"
