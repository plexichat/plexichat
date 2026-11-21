"""MESSAGE_ACK on a *server* channel must not cross a block relationship.

Read receipts are fanned out with an explicit recipient list, which bypasses
the gateway router's DM block filter. The read-receipt routes already apply a
two-way block gate on the DM/participant path, but their server-channel path
resolves recipients through ``resolve_channel_event_recipients`` (viewers with
``channels.view``) and dispatched that list verbatim. A user who blocked the
actor -- or whom the actor blocked -- can share a server, so they stayed in
the viewer set and kept receiving the actor's read receipts even though the
identical event on a DM channel was withheld from them.

This pins the same two-way gate on the server-channel branch for both the
single-channel ACK route and the shared bulk-ACK broadcast helper. A failed
block lookup must fail closed (empty audience).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes.messages.read_receipts import (
    acknowledge_messages,
    _dispatch_message_ack_broadcast,
)
from src.core.auth.models import AccountType, TokenInfo

pytestmark = pytest.mark.api

ACTOR = 1
BLOCKER = 3  # blocked the actor
BLOCKED = 5  # blocked by the actor
NEUTRAL = 2
SERVER_ID = 777
CHANNEL_ID = 555


def _token(uid: int) -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=f"user{uid}",
        account_type=AccountType.USER,
    )


class _Relationships:
    """Two-way block set (get_all_blocked_ids is the authoritative helper)."""

    def __init__(self, blocked=None, explode=False):
        self.blocked = set(blocked or [])
        self.explode = explode
        self.lookups: list = []

    def get_all_blocked_ids(self, user_id):
        self.lookups.append(user_id)
        if self.explode:
            raise RuntimeError("relationship store unavailable")
        return list(self.blocked)


class _Channel:
    server_id = SERVER_ID
    channel_type = "text"


class _Servers:
    def get_channel(self, channel_id, user_id=None):
        return _Channel()

    def get_member_user_ids(self, server_id, exclude_user_id=None):
        return [ACTOR, NEUTRAL, BLOCKER, BLOCKED]

    def has_permission(self, user_id, server_id, perm, channel_id):
        return True


class _Messaging:
    def get_manager(self):
        return self

    def mark_read(self, uid, cid, mid):
        return 1


class _Recorder:
    def __init__(self):
        self.dispatched: list = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, list(user_ids)))


async def _drain_tasks() -> None:
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_caches():
    from src.core.events.gateway_emit import clear_channel_recipient_cache

    clear_channel_recipient_cache()
    yield
    clear_channel_recipient_cache()


def _wire(monkeypatch, rel, recorder) -> None:
    servers = _Servers()
    messaging = _Messaging()

    monkeypatch.setattr(api_module, "get_servers", lambda: servers)
    monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api_module, "get_notifications", lambda: None)
    monkeypatch.setattr(api_module, "get_db", lambda: None)
    monkeypatch.setattr(
        "src.core.messaging.mark_read", messaging.mark_read, raising=False
    )
    monkeypatch.setattr(
        "src.core.messaging.get_user_message_settings",
        lambda uid: SimpleNamespace(read_receipts_enabled=True),
        raising=False,
    )

    import src.core.relationships as rel_module

    monkeypatch.setattr(rel_module, "_setup_complete", True, raising=False)
    monkeypatch.setattr(rel_module, "_manager", object(), raising=False)
    monkeypatch.setattr(rel_module, "get_all_blocked_ids", rel.get_all_blocked_ids)

    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)


@pytest.mark.asyncio
async def test_server_channel_ack_excludes_blocked_viewers(monkeypatch):
    rel = _Relationships(blocked=[BLOCKER, BLOCKED])
    recorder = _Recorder()
    _wire(monkeypatch, rel, recorder)

    result = await acknowledge_messages(
        str(CHANNEL_ID), message_id=None, current_user=_token(ACTOR)
    )
    await _drain_tasks()

    assert result.success is True
    assert len(recorder.dispatched) == 1, "the ACK should still be broadcast"
    _event, recipients = recorder.dispatched[0]
    assert set(recipients) == {NEUTRAL}, (
        "a viewer who blocked the actor, or was blocked by them, must not "
        f"receive the server-channel read receipt; got {recipients}"
    )
    assert rel.lookups == [ACTOR], "the two-way block set is resolved for the actor"


@pytest.mark.asyncio
async def test_bulk_server_channel_ack_excludes_blocked_viewers(monkeypatch):
    rel = _Relationships(blocked=[BLOCKER])
    recorder = _Recorder()
    _wire(monkeypatch, rel, recorder)

    await _dispatch_message_ack_broadcast(
        channel_id=CHANNEL_ID, message_id=99, current_user_id=ACTOR
    )
    await _drain_tasks()

    assert len(recorder.dispatched) == 1
    _event, recipients = recorder.dispatched[0]
    assert set(recipients) == {NEUTRAL, BLOCKED}, (
        "the blocked viewer must be dropped from the bulk server-channel ACK"
    )


@pytest.mark.asyncio
async def test_server_channel_ack_fails_closed_on_block_lookup_error(monkeypatch):
    rel = _Relationships(explode=True)
    recorder = _Recorder()
    _wire(monkeypatch, rel, recorder)

    await acknowledge_messages(
        str(CHANNEL_ID), message_id=None, current_user=_token(ACTOR)
    )
    await _drain_tasks()

    assert recorder.dispatched == [], (
        "an unresolvable block set must withhold the read receipt entirely"
    )
