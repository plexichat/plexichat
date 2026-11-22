"""Expired invites must not leak metadata through the invite-info endpoint."""

from __future__ import annotations
import time
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from fastapi import HTTPException
import src.api as api
from src.api.routes.channels.invites import ChannelInvitesMixin
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _user(user_id: int = 111) -> TokenInfo:
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


def _invite(expires_at):
    return SimpleNamespace(
        code="ABC12345",
        server_id=987654321,
        channel_id=555,
        inviter_id=42,
        uses=3,
        max_uses=10,
        expires_at=expires_at,
        server_name=None,
    )


class _Servers:
    def __init__(self, invite):
        self._invite = invite

    def get_invite(self, code):
        return self._invite


async def test_expired_invite_info_is_404():
    now_ms = int(time.time() * 1000)
    with patch.object(api, "get_servers", return_value=_Servers(_invite(now_ms - 1))):
        with pytest.raises(HTTPException) as exc:
            await ChannelInvitesMixin()._get_invite_info("ABC12345", _user())
    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["message"] == "Invite not found or expired"


async def test_live_invite_info_still_served():
    now_ms = int(time.time() * 1000)
    with patch.object(
        api, "get_servers", return_value=_Servers(_invite(now_ms + 60_000))
    ):
        result = await ChannelInvitesMixin()._get_invite_info("ABC12345", _user())
    assert result.code == "ABC12345"
    assert str(result.server_id) == str(987654321)
    assert str(result.inviter_id) == str(42)
