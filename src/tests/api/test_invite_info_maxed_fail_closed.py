"""Exhausted (maxed-out) invites must not leak metadata through invite info.

``get_invite`` deliberately keeps revoked/maxed/expired rows so the join path
can report its 410. The invite-info endpoint promises 404 for "not found or
expired" and already fails closed for revoked (core row filter) and expired
(the explicit epoch-ms guard). An invite whose ``uses`` reached ``max_uses``
is just as unusable, but used to still return the server id/name, channel id,
inviter id and use counters to anyone holding the code. These tests pin that
gap shut without changing the live/unlimited invite behaviour.
"""

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


def _invite(*, uses: int, max_uses: int) -> SimpleNamespace:
    # Far-future expiry so only the max-uses condition can trigger the guard.
    return SimpleNamespace(
        code="MAXED123",
        server_id=987654321,
        channel_id=555,
        inviter_id=42,
        uses=uses,
        max_uses=max_uses,
        expires_at=int(time.time() * 1000) + 600_000,
        server_name="Secret Server",
    )


class _Servers:
    def __init__(self, invite):
        self._invite = invite

    def get_invite(self, code):
        return self._invite


async def _info(invite):
    with patch.object(api, "get_servers", return_value=_Servers(invite)):
        return await ChannelInvitesMixin()._get_invite_info("MAXED123", _user())


async def test_maxed_invite_info_is_404():
    """uses == max_uses: the invite is exhausted, no metadata may escape."""
    with pytest.raises(HTTPException) as exc:
        await _info(_invite(uses=10, max_uses=10))
    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["message"] == "Invite not found or expired"


async def test_over_max_invite_info_is_404():
    """uses > max_uses (e.g. after a racing redeem): still fail closed."""
    with pytest.raises(HTTPException) as exc:
        await _info(_invite(uses=11, max_uses=10))
    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["message"] == "Invite not found or expired"


async def test_live_limited_invite_info_still_served():
    """A limited invite with uses left keeps serving its preview metadata."""
    result = await _info(_invite(uses=9, max_uses=10))
    assert result.code == "MAXED123"
    assert str(result.server_id) == str(987654321)
    assert str(result.channel_id) == str(555)
    assert str(result.inviter_id) == str(42)
    assert result.server_name == "Secret Server"


async def test_unlimited_invite_info_still_served():
    """max_uses == 0 means unlimited: high use counts must not be rejected."""
    result = await _info(_invite(uses=9999, max_uses=0))
    assert result.code == "MAXED123"
    assert str(result.inviter_id) == str(42)
