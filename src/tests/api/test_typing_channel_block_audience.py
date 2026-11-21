"""Server-channel typing frames must not reach users who block the sender.

Typing events are dispatched with an *explicit* recipient list, so the gateway
router's DM block filter (implicit routing only) never sees them. The REST
typing endpoint derives a server-channel audience from channel-view permission
alone; a pair with a block relationship can still share a server. Without an
explicit two-way block gate a blocked user keeps receiving the sender's
TYPING_START frames -- the same presence leak the DM path already guards.

Run with: pytest src/tests/api/test_typing_channel_block_audience.py -q
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes.messages.typing import trigger_typing
from src.api.websocket.handlers import presence as hp
from src.core.auth.models import AccountType, TokenInfo


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
    def __init__(self, blocked=None, explode=False):
        self._blocked = set(blocked or [])
        self._explode = explode
        self.lookups: list[int] = []

    def get_all_blocked_ids(self, user_id):
        self.lookups.append(int(user_id))
        if self._explode:
            raise RuntimeError("relationship store unavailable")
        return list(self._blocked)


class _Presence:
    def __init__(self):
        self.started: list[tuple[int, int]] = []

    def start_typing(self, user_id, channel_id):
        self.started.append((user_id, channel_id))


class _Recorder:
    def __init__(self):
        self.dispatched: list[tuple[object, list[int]]] = []

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


def _wire(monkeypatch, rel, recipients) -> tuple[_Presence, _Recorder]:
    presence = _Presence()
    recorder = _Recorder()
    channel = SimpleNamespace(id=500, server_id=100, channel_type=None)
    # The real resolver excludes the sender from the audience; mirror that.
    recipients = [uid for uid in recipients if uid != 1]

    monkeypatch.setattr(api_module, "get_presence", lambda: presence)
    monkeypatch.setattr(
        api_module,
        "get_servers",
        lambda: SimpleNamespace(
            get_channel=lambda cid, uid: channel,
            channel_exists=lambda cid: True,
        ),
    )
    monkeypatch.setattr(api_module, "get_messaging", lambda: None)
    monkeypatch.setattr(
        hp, "_get_channel_recipients", lambda uid, cid: (list(recipients), 100)
    )

    import src.core.relationships as rel_module

    monkeypatch.setattr(rel_module, "_setup_complete", True, raising=False)
    monkeypatch.setattr(rel_module, "_manager", object(), raising=False)
    monkeypatch.setattr(rel_module, "get_all_blocked_ids", rel.get_all_blocked_ids)

    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)
    return presence, recorder


@pytest.mark.api
@pytest.mark.asyncio
async def test_server_typing_excludes_blocked_member(monkeypatch) -> None:
    """A blocked pair sharing a server channel must not see each other's typing."""
    rel = _Relationships(blocked=[3])  # actor 1 <-> user 3 blocked (two-way)
    _presence, recorder = _wire(monkeypatch, rel, recipients=[1, 2, 3])

    result = await trigger_typing("500", _token(1))
    await _drain_tasks()

    assert result.success is True
    assert len(recorder.dispatched) == 1
    _event, recipients = recorder.dispatched[0]
    assert set(recipients) == {2}, "blocked user 3 must not receive typing"
    assert rel.lookups == [1], "the actor's two-way block set must be consulted"


@pytest.mark.api
@pytest.mark.asyncio
async def test_server_typing_keeps_unblocked_members(monkeypatch) -> None:
    """Without a block relationship the channel audience is unchanged."""
    rel = _Relationships()
    _presence, recorder = _wire(monkeypatch, rel, recipients=[1, 2, 3])

    await trigger_typing("500", _token(1))
    await _drain_tasks()

    assert len(recorder.dispatched) == 1
    assert set(recorder.dispatched[0][1]) == {2, 3}
    assert rel.lookups == [1]


@pytest.mark.api
@pytest.mark.asyncio
async def test_server_typing_fails_closed_on_block_lookup_error(monkeypatch) -> None:
    """A broken block store must withhold the audience, not widen it."""
    rel = _Relationships(explode=True)
    _presence, recorder = _wire(monkeypatch, rel, recipients=[1, 2, 3])

    await trigger_typing("500", _token(1))
    await _drain_tasks()

    assert recorder.dispatched == []
