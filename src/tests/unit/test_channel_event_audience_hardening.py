"""Regressions for channel-event audience scoping and bulk-ACK body bounds.

* CHANNEL_UPDATE carries the full channel representation (name, topic, ...).
  It must reach only members who can view the channel, not the raw server
  member list, or a restricted channel's existence/settings leak.
* CHANNEL_DELETE must use the channel's pre-deletion viewer set.
* The bulk-ACK id map must be byte-capped before Pydantic parses it.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes.channels.crud import ChannelCRUDMixin
from src.api.routes.messages import read_receipts
from src.api.schemas.servers import ChannelUpdateRequest
from src.core.auth.models import AccountType, TokenInfo
from src.core.events import gateway_emit


def _token(uid=1):
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


def _channel():
    return SimpleNamespace(
        id=100,
        server_id=7,
        name="private-notes",
        topic="secret roadmap",
        channel_type="text",
        position=0,
        category_id=None,
        nsfw=False,
        slowmode_seconds=0,
        read_receipts_enabled=True,
        created_at=1_700_000_000_000,
    )


class _Servers:
    def __init__(self, channel):
        self.channel = channel
        self.deleted = []

    def channel_exists(self, cid):
        return True

    def get_channel(self, cid, user_id):
        return self.channel

    def update_channel(self, user_id, cid, **kwargs):
        return self.channel

    def delete_channel(self, user_id, cid):
        self.deleted.append(cid)
        return True

    def get_member_user_ids(self, sid):
        raise AssertionError(
            "raw server member list must not be used as an event audience"
        )


class _Recorder:
    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, list(user_ids)))


async def _drain():
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
def wire(monkeypatch):
    servers = _Servers(_channel())
    recorder = _Recorder()
    monkeypatch.setattr(api_module, "get_servers", lambda: servers)
    monkeypatch.setattr(api_module, "get_db", lambda: None)
    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)
    # Only user 2 can view the restricted channel; user 3 is a server member
    # who cannot. The resolver is the single source of truth for the audience.
    monkeypatch.setattr(
        gateway_emit,
        "resolve_channel_event_recipients",
        lambda cid, **kwargs: [2],
    )
    return servers, recorder


@pytest.mark.asyncio
async def test_channel_update_event_is_scoped_to_viewers(monkeypatch, wire):
    servers, recorder = wire
    await ChannelCRUDMixin()._update_channel(
        "100",
        ChannelUpdateRequest(name="private-notes", topic="secret roadmap"),
        current_user=_token(),
    )
    await _drain()

    assert len(recorder.dispatched) == 1
    event, recipients = recorder.dispatched[0]
    assert event.event_type.value == "CHANNEL_UPDATE"
    assert set(recipients) == {2}, "non-viewers must not receive channel metadata"


@pytest.mark.asyncio
async def test_channel_delete_event_uses_pre_delete_viewer_scope(monkeypatch, wire):
    servers, recorder = wire
    await ChannelCRUDMixin()._delete_channel("100", current_user=_token())
    await _drain()

    assert servers.deleted == [100]
    assert len(recorder.dispatched) == 1
    event, recipients = recorder.dispatched[0]
    assert event.event_type.value == "CHANNEL_DELETE"
    assert set(recipients) == {2}


def test_bulk_ack_route_installs_the_body_cap():
    route = next(
        r
        for r in read_receipts.router.routes
        if getattr(r, "path", None) == "/channels/ack/bulk"
    )
    calls = [d.call for d in getattr(route, "dependant").dependencies]
    assert read_receipts._cap_bulk_ack_body in calls
    assert 0 < read_receipts.MAX_BULK_ACK_BODY <= 1024 * 1024


def test_bulk_ack_cap_rejects_oversized_body():
    class _Request:
        headers = {"content-length": str(read_receipts.MAX_BULK_ACK_BODY + 1)}

        async def body(self):
            return b"x"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_receipts._cap_bulk_ack_body(_Request()))
    assert exc.value.status_code == 413
