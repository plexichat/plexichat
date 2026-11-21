"""Typing gate cannot be bypassed with a channel's conversation-id alias.

Message routes accept a server channel's linked ``conversation_id`` as a URL
alias for the channel id. ``get_channel`` / ``channel_exists`` only recognise
channel ids, so a member denied ``channels.view`` on a private channel can put
that conversation id in ``POST /channels/{channel_id}/typing`` and fall through
to the DM audience path -- broadcasting their typing to every conversation
participant. The route must resolve the alias membership-agnostically and apply
the live channel gate (and channel-view-filtered audience) on the real channel.

Run with: pytest src/tests/api/test_typing_conversation_id_alias_gate.py -q
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

REAL_CHANNEL = 700
SERVER_ID = 800
ALIAS_CONVERSATION = 900
ACTOR = 1
VIEWER = 2


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


class _Presence:
    def __init__(self) -> None:
        self.started: list[tuple[int, int]] = []

    def start_typing(self, user_id, channel_id) -> None:
        self.started.append((int(user_id), int(channel_id)))


class _Recorder:
    def __init__(self) -> None:
        self.dispatched: list[tuple[object, list[int]]] = []

    async def dispatch_event(self, event, user_ids) -> None:
        self.dispatched.append((event, list(user_ids)))


class _Messaging:
    """DM participant lookup would hand the alias path an unfiltered audience."""

    def get_participants(self, user_id, cid):
        return [
            SimpleNamespace(user_id=ACTOR),
            SimpleNamespace(user_id=VIEWER),
        ]


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


def _wire(monkeypatch, *, can_view: bool):
    """Servers store where only the real channel id is a recognised channel.

    ``get_channel`` grants view solely to ``can_view``; ``channel_exists`` is
    False for the alias (the exact condition that used to open the DM path).
    """
    channel = SimpleNamespace(id=REAL_CHANNEL, server_id=SERVER_ID, channel_type=None)
    recipient_calls: list[int] = []

    def get_channel(chid, uid):
        return channel if (can_view and int(chid) == REAL_CHANNEL) else None

    def channel_exists(chid):
        return int(chid) == REAL_CHANNEL

    def get_channel_by_conversation(cid):
        if int(cid) == ALIAS_CONVERSATION:
            return {"id": REAL_CHANNEL, "server_id": SERVER_ID}
        return None

    def channel_recipients(uid, cid):
        recipient_calls.append(int(cid))
        return [VIEWER], SERVER_ID

    presence = _Presence()
    recorder = _Recorder()
    monkeypatch.setattr(api_module, "get_presence", lambda: presence)
    monkeypatch.setattr(
        api_module,
        "get_servers",
        lambda: SimpleNamespace(
            get_channel=get_channel,
            channel_exists=channel_exists,
            get_channel_by_conversation=get_channel_by_conversation,
        ),
    )
    monkeypatch.setattr(api_module, "get_messaging", lambda: _Messaging())
    monkeypatch.setattr(hp, "_get_channel_recipients", channel_recipients)

    import src.core.relationships as rel_module

    monkeypatch.setattr(rel_module, "_setup_complete", False, raising=False)

    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)
    return presence, recorder, recipient_calls


@pytest.mark.api
@pytest.mark.asyncio
async def test_barred_member_cannot_type_via_conversation_id_alias(monkeypatch) -> None:
    """The alias must not reopen the DM audience for a channels.view-barred user."""
    presence, recorder, recipient_calls = _wire(monkeypatch, can_view=False)

    result = await trigger_typing(str(ALIAS_CONVERSATION), _token(ACTOR))
    await _drain_tasks()

    assert result.success is True
    assert recorder.dispatched == [], (
        "typing leaked into a channel the actor cannot view"
    )
    assert presence.started == []
    assert recipient_calls == []


@pytest.mark.api
@pytest.mark.asyncio
async def test_live_gate_also_denies_the_real_channel_id(monkeypatch) -> None:
    """Sanity: the real channel id is already denied -- the alias was the bypass."""
    _presence, recorder, recipient_calls = _wire(monkeypatch, can_view=False)

    await trigger_typing(str(REAL_CHANNEL), _token(ACTOR))
    await _drain_tasks()

    assert recorder.dispatched == []
    assert recipient_calls == []


@pytest.mark.api
@pytest.mark.asyncio
async def test_viewer_using_alias_gets_channel_filtered_audience(monkeypatch) -> None:
    """A member who CAN view is still served, with the real channel's audience."""
    _presence, recorder, recipient_calls = _wire(monkeypatch, can_view=True)

    await trigger_typing(str(ALIAS_CONVERSATION), _token(ACTOR))
    await _drain_tasks()

    assert recipient_calls == [REAL_CHANNEL]
    assert len(recorder.dispatched) == 1
    _event, recipients = recorder.dispatched[0]
    assert set(recipients) == {VIEWER}
