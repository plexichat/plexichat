"""Regressions for fail-open server-channel -> DM audience fallbacks.

Two message-route audience resolvers used to treat *any* ``get_channel`` miss
as "this is a DM" and fall through to the raw conversation participant list.
``get_channel`` also returns None when the actor lost access, the channel was
deleted, or the lookup itself errored, so a restricted server channel could
have its content/typing frames delivered to every conversation participant
without the per-member ``channels.view`` filter.

Both resolvers now probe the channel id membership-agnostically and fail
closed: a server channel whose viewer walk failed broadcasts to nobody.
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes.messages.messages_crud.broadcast import _resolve_recipients
from src.api.routes.messages.typing import trigger_typing
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


class _UnresolvedServerChannelServers:
    """Servers module where ``get_channel`` misses for the actor.

    ``channel_exists`` is membership-agnostic: it still reports the id as a
    server channel even when the actor cannot resolve it through get_channel.
    """

    def __init__(self, exists=True, explode=False):
        self.exists = exists
        self.explode = explode
        self.calls = []

    def get_channel(self, channel_id, actor_id):
        self.calls.append(("get_channel", channel_id, actor_id))
        return None

    def channel_exists(self, channel_id):
        self.calls.append(("channel_exists", channel_id))
        if self.explode:
            raise RuntimeError("channel store unavailable")
        return self.exists


class _CountingMessaging:
    """Messaging module that records whether the DM participant fallback ran."""

    def __init__(self, participants):
        self.participants = participants
        self.calls = []

    def get_participants(self, actor_id, conversation_id):
        self.calls.append((actor_id, conversation_id))
        return [SimpleNamespace(user_id=uid) for uid in self.participants]


class _Recorder:
    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, list(user_ids)))


async def _drain_tasks():
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.api
class TestBroadcastAudienceUnresolvedChannel:
    def test_unresolved_server_channel_does_not_leak_to_participants(self):
        """A get_channel miss on a real server channel must not use DM fans."""
        servers = _UnresolvedServerChannelServers(exists=True)
        messaging = _CountingMessaging([2, 3, 4])

        ids, server_id = _resolve_recipients(servers, messaging, 99, 99, actor_id=1)

        assert ids == []
        assert server_id is None
        assert messaging.calls == [], "server channel leaked into DM fallback"

    def test_failed_existence_probe_fails_closed(self):
        """An unproven channel id is treated as a server channel (no leak)."""
        servers = _UnresolvedServerChannelServers(explode=True)
        messaging = _CountingMessaging([2, 3, 4])

        ids, _ = _resolve_recipients(servers, messaging, 99, 99, actor_id=1)

        assert ids == []
        assert messaging.calls == []

    def test_real_dm_still_uses_participants(self, monkeypatch):
        """No regression for genuine DMs: participants are still returned."""
        # The ambient relationship source may be a half-initialised module
        # whose block lookup fails closed; keep this a pure audience-scope
        # test by removing that orthogonal filter.
        monkeypatch.setattr(api_module, "get_relationships", lambda: None)
        servers = _UnresolvedServerChannelServers(exists=False)
        messaging = _CountingMessaging([11, 22])

        ids, server_id = _resolve_recipients(servers, messaging, 55, 55, actor_id=11)

        assert ids == [11, 22]
        assert server_id is None
        assert messaging.calls == [(11, 55)]


@pytest.mark.api
class TestTypingAudienceUnresolvedChannel:
    @pytest.mark.asyncio
    async def test_unresolved_server_channel_does_not_broadcast_typing(
        self, monkeypatch
    ):
        """A get_channel miss on a server channel must not leak typing frames."""
        servers = _UnresolvedServerChannelServers(exists=True)
        messaging = _CountingMessaging([2, 3])
        recorder = _Recorder()

        monkeypatch.setattr(api_module, "get_servers", lambda: servers)
        monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)
        monkeypatch.setattr(api_module, "get_presence", lambda: None)
        monkeypatch.setattr(ws_module, "is_setup", lambda: True)
        monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)

        result = await trigger_typing("99", _token(1))
        await _drain_tasks()

        assert result.success is True
        assert recorder.dispatched == [], "typing leaked to DM participants"
        assert messaging.calls == []

    @pytest.mark.asyncio
    async def test_real_dm_still_broadcasts_typing(self, monkeypatch):
        """No regression for genuine DMs: typing still reaches participants."""
        servers = _UnresolvedServerChannelServers(exists=False)
        messaging = _CountingMessaging([1, 2])
        recorder = _Recorder()

        monkeypatch.setattr(api_module, "get_servers", lambda: servers)
        monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)
        monkeypatch.setattr(api_module, "get_presence", lambda: None)
        monkeypatch.setattr(ws_module, "is_setup", lambda: True)
        monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)

        await trigger_typing("55", _token(1))
        await _drain_tasks()

        assert len(recorder.dispatched) == 1
        _event, recipients = recorder.dispatched[0]
        assert recipients == [2]
