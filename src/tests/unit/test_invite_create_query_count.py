"""The invite-create dual probe must not issue a channel query on success.

Before this change ``_create_channel_invite`` ran ``channel_exists`` and then
``get_channel`` on every request. ``channel_exists`` is an uncached raw
``SELECT EXISTS`` while ``get_channel`` is cache-backed, so the probe added a
real database round trip to every successful invite creation. It is now only
consulted when ``get_channel`` declines, to keep the 404-vs-403 contract. These
tests pin the call counts for all three outcomes.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.channels.invites import ChannelInvitesMixin
from src.api.schemas.channels import ChannelInviteCreateRequest
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_CHANNEL_ID = 123456789


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


def _body() -> ChannelInviteCreateRequest:
    return ChannelInviteCreateRequest(max_age=3600, max_uses=1, temporary=False)


class _CountingServers:
    def __init__(self, channel, exists):
        self.channel = channel
        self.exists = exists
        self.calls = []

    def get_channel(self, channel_id, user_id):
        self.calls.append("get_channel")
        return self.channel

    def channel_exists(self, channel_id):
        self.calls.append("channel_exists")
        return self.exists

    def create_invite(self, **kwargs):
        self.calls.append("create_invite")
        return SimpleNamespace(code="ABC12345", server_id=555, created_at=1)


async def test_success_uses_one_channel_lookup_and_no_existence_probe():
    servers = _CountingServers(channel=SimpleNamespace(id=_CHANNEL_ID), exists=True)

    with (
        patch.object(api, "get_servers", return_value=servers),
        patch("src.core.events.gateway_emit.emit_invite_create"),
    ):
        resp = await ChannelInvitesMixin()._create_channel_invite(
            str(_CHANNEL_ID), _body(), _user()
        )

    assert resp.code == "ABC12345"
    assert servers.calls == ["get_channel", "create_invite"]


async def test_missing_channel_still_returns_404_with_both_probes():
    servers = _CountingServers(channel=None, exists=False)

    with patch.object(api, "get_servers", return_value=servers):
        with pytest.raises(HTTPException) as ei:
            await ChannelInvitesMixin()._create_channel_invite(
                str(_CHANNEL_ID), _body(), _user()
            )

    assert ei.value.status_code == 404
    assert servers.calls == ["get_channel", "channel_exists"]


async def test_blocked_channel_still_returns_403_with_both_probes():
    servers = _CountingServers(channel=None, exists=True)

    with patch.object(api, "get_servers", return_value=servers):
        with pytest.raises(HTTPException) as ei:
            await ChannelInvitesMixin()._create_channel_invite(
                str(_CHANNEL_ID), _body(), _user()
            )

    assert ei.value.status_code == 403
    assert servers.calls == ["get_channel", "channel_exists"]
