"""Invite routes validate once and batch join fan-out reads concurrently.

BEFORE this change ``_get_invite_info`` ran ``require_target_user_id`` twice for
the same inviter id, and the join event dispatcher issued three concurrent
storage reads followed by a sequential fourth, then truncated ``channels`` /
``roles`` six times and ``member_ids`` six times each (all idempotent). These
tests pin the single-pass behaviour and the one-read-per-collection fan-out.
"""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.api as api
import src.api.routes.channels.invites as invites_mod
from src.api.routes.channels.helpers import MAX_BULK_BATCH
from src.api.routes.channels.invites import ChannelInvitesMixin
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _user(user_id: int = 42) -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


async def test_invite_info_validates_inviter_only_once():
    invite = SimpleNamespace(
        code="CODE1234",
        server_id=555,
        server_name="A server",
        channel_id=777,
        inviter_id=888,
        uses=1,
        max_uses=2,
        expires_at=None,
    )

    class _Servers:
        def get_invite(self, code):
            return invite

        def get_server(self, *args, **kwargs):
            raise AssertionError("server_name is present; no get_server lookup needed")

    calls = []
    real_validate = invites_mod.require_target_user_id

    def _spy(value, field="user_id"):
        calls.append(field)
        return real_validate(value, field)

    with (
        patch.object(invites_mod, "require_target_user_id", _spy),
        patch.object(api, "get_servers", return_value=_Servers()),
    ):
        resp = await ChannelInvitesMixin()._get_invite_info("CODE1234", _user())

    assert calls == ["inviter_id"]
    assert resp.inviter_id is not None
    assert int(resp.inviter_id) == 888


class _CountingServers:
    def __init__(self, member_ids):
        self.member_ids = member_ids
        self.calls = []

    def get_server(self, sid, uid):
        self.calls.append("get_server")
        return SimpleNamespace(name="srv", owner_id=7, member_count=3)

    def get_channels(self, uid, sid):
        self.calls.append("get_channels")
        ch = SimpleNamespace(id=1, name="general", channel_type="text", position=0)
        return [ch]

    def get_roles(self, uid, sid):
        self.calls.append("get_roles")
        role = SimpleNamespace(id=2, name="member", color=0, hoist=False, position=0)
        return [role]

    def get_member_user_ids(self, sid, exclude_user_id=None):
        self.calls.append("get_member_user_ids")
        return self.member_ids


class _Dispatcher:
    def __init__(self):
        self.events = []

    async def dispatch_event(self, event, recipients):
        self.events.append((event, list(recipients)))


async def test_join_dispatch_reads_each_collection_once():
    dispatcher = _Dispatcher()
    servers = _CountingServers(member_ids=[10, 11, 12])

    with (
        patch("src.api.websocket.is_setup", return_value=True),
        patch("src.api.websocket.get_dispatcher", return_value=dispatcher),
        patch.object(api, "get_auth", return_value=None),
    ):
        await ChannelInvitesMixin()._dispatch_invite_join_events(servers, 999, _user())

    # Concurrent thread-pool execution does not guarantee order; each read
    # must happen exactly once (the pre-change code did channels/roles six
    # times each via repeated bounded_bulk_batch calls, but those were local
    # truncations, and member_ids was fetched after the other three).
    assert Counter(servers.calls) == Counter(
        {
            "get_server": 1,
            "get_channels": 1,
            "get_roles": 1,
            "get_member_user_ids": 1,
        }
    )
    # GUILD_CREATE (to the joiner) + GUILD_MEMBER_ADD (to existing members).
    assert len(dispatcher.events) == 2
    assert dispatcher.events[1][1] == [10, 11, 12]


async def test_join_dispatch_caps_member_ids_in_a_single_pass():
    dispatcher = _Dispatcher()
    servers = _CountingServers(member_ids=list(range(1, MAX_BULK_BATCH + 6)))

    with (
        patch("src.api.websocket.is_setup", return_value=True),
        patch("src.api.websocket.get_dispatcher", return_value=dispatcher),
        patch.object(api, "get_auth", return_value=None),
    ):
        await ChannelInvitesMixin()._dispatch_invite_join_events(servers, 999, _user())

    assert len(dispatcher.events[1][1]) == MAX_BULK_BATCH
