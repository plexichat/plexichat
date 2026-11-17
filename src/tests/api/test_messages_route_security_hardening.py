"""Security regressions for message routes.

Covers three hardening fixes:

* pin/unpin must reject a message whose conversation is not the addressed
  channel (cross-channel IDOR / content leak via path ids);
* pin/unpin broadcasts must be filtered by ``channels.view`` instead of
  fanned out to the entire server member list;
* embed suppression must strip the metadata embed payload as well as the
  top-level ``embeds`` array (otherwise moderation suppression is a no-op).
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes.messages.helpers import _message_to_response
from src.api.routes.messages.pins import pin_message, unpin_message
from src.core.auth.models import AccountType, TokenInfo


def _token(uid):
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


def _message(mid, conv_id, metadata=None, embeds=None):
    return SimpleNamespace(
        id=mid,
        conversation_id=conv_id,
        author_id=1,
        content="secret cross-channel content",
        created_at=1_700_000_000_000,
        attachments=[],
        embeds=embeds or [],
        pinned=False,
        status=None,
        metadata=metadata,
        channel_id=conv_id,
    )


class _Messaging:
    def __init__(self, msg):
        self.msg = msg
        self.pin_calls = []
        self.unpin_calls = []

    def get_message(self, user_id, mid):
        return self.msg

    def pin_message(self, user_id, mid):
        self.pin_calls.append(mid)
        return True

    def unpin_message(self, user_id, mid):
        self.unpin_calls.append(mid)
        return True

    def get_participants(self, user_id, cid):
        return []


class _Servers:
    def __init__(self, linked=None, members=None, allow=None, channel=None):
        self.linked = linked or {}
        self.members = members or []
        self.allow = allow or set()
        self.channel = channel

    def get_channel_conversation_id(self, cid):
        return self.linked.get(cid)

    def get_channel(self, cid, user_id):
        return self.channel

    def get_member_user_ids(self, sid):
        return list(self.members)

    def has_permission(self, user_id, sid, permission, cid):
        return user_id in self.allow


class _Recorder:
    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, list(user_ids)))


async def _drain_broadcast_tasks():
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_pin_rejects_message_from_a_foreign_channel(monkeypatch):
    """A message in conversation 999 cannot be pinned via channel 100."""
    messaging = _Messaging(_message(555, 999))
    servers = _Servers(linked={100: 111})  # channel 100 -> conversation 111
    monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api_module, "get_servers", lambda: servers)

    with pytest.raises(HTTPException) as exc:
        await pin_message("100", "555", current_user=_token(1))

    assert exc.value.status_code == 404
    assert messaging.pin_calls == [], "no mutation before channel binding"


@pytest.mark.asyncio
async def test_unpin_rejects_message_from_a_foreign_channel(monkeypatch):
    messaging = _Messaging(_message(556, 999))
    servers = _Servers(linked={100: 111})
    monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api_module, "get_servers", lambda: servers)

    with pytest.raises(HTTPException) as exc:
        await unpin_message("100", "556", current_user=_token(1))

    assert exc.value.status_code == 404
    assert messaging.unpin_calls == []


@pytest.mark.asyncio
async def test_pin_broadcast_respects_channels_view_permission(monkeypatch):
    """Pin content must not reach server members who cannot view the channel."""
    messaging = _Messaging(_message(555, 111))
    channel = SimpleNamespace(server_id=7, conversation_id=111)
    # Members 1 and 2 are in the server, but only member 1 can view channel 100.
    servers = _Servers(
        linked={100: 111},
        members=[1, 2],
        allow={1},
        channel=channel,
    )
    monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api_module, "get_servers", lambda: servers)
    recorder = _Recorder()
    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)

    response = await pin_message("100", "555", current_user=_token(1))
    assert response.success is True
    await _drain_broadcast_tasks()

    assert len(recorder.dispatched) == 1
    _event, recipients = recorder.dispatched[0]
    assert set(recipients) == {1}, "member 2 lacks channels.view"


def test_suppressed_embeds_are_stripped_from_metadata(monkeypatch):
    """Suppression must remove metadata['embeds'], which clients render."""
    monkeypatch.setattr(api_module, "get_embeds", lambda: None)
    msg = _message(
        101,
        303,
        metadata={"embeds": [{"title": "hidden preview"}], "keep": "value"},
        embeds=[{"title": "hidden preview"}],
    )

    resp = _message_to_response(msg, viewer_user_id=None, suppress_embeds=True)

    assert resp.embeds == []
    assert resp.metadata["embeds_suppressed"] is True
    assert "embeds" not in resp.metadata
    assert resp.metadata["keep"] == "value"


def test_unsuppressed_embeds_remain_in_metadata(monkeypatch):
    """The strip must only apply to suppressed messages."""
    monkeypatch.setattr(api_module, "get_embeds", lambda: None)
    embeds = [{"title": "visible preview"}]
    msg = _message(102, 303, metadata={"embeds": embeds}, embeds=embeds)

    resp = _message_to_response(msg, viewer_user_id=None, suppress_embeds=False)

    assert resp.embeds == embeds
    assert resp.metadata["embeds"] == embeds
