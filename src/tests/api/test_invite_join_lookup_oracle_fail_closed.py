"""The invite-join route must not leak an invite-code existence oracle.

``GET /channels/invites/{code}`` deliberately fails closed with a single static
404 for *every* unusable state (never existed, revoked, expired, exhausted), so
a non-member cannot use it to tell whether a guessed code is real.  The join
route used to break that invariant: ``use_invite`` distinguishes expired /
maxed-out rows (which it keeps for this purpose) from codes that never existed
or were revoked, and the old error map surfaced that as ``410`` vs ``404``.  A
client brute-forcing codes could therefore confirm that a code was once valid.

These tests pin all unusable states to the *same* 404/status/body, while
checking that a genuinely usable invite still joins (so the fix fails closed
without weakening the real redemption path).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.channels.invites import ChannelInvitesMixin
from src.core.auth.models import AccountType, TokenInfo
from src.core.servers import exceptions as srv

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


class _FailingServers:
    """Core module that always rejects redemption with ``exc``."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    def use_invite(self, user_id, code):
        raise self._exc


class _WorkingServers:
    def use_invite(self, user_id, code):
        return cast(Any, type("Member", (), {"server_id": 987654321})())


async def _join(exc: BaseException) -> HTTPException:
    with patch.object(api, "get_servers", return_value=_FailingServers(exc)):
        with pytest.raises(HTTPException) as caught:
            await ChannelInvitesMixin()._join_server_via_invite("GUESSED1", _user())
    return caught.value


@pytest.mark.parametrize(
    "exc",
    [
        srv.InviteNotFoundError("Invite not found"),
        srv.InviteExpiredError("Invite has expired", expired_at=1704153600000),
        srv.InviteMaxUsesError(
            "Invite has reached maximum uses", max_uses=5, current_uses=5
        ),
    ],
    ids=["never-existed", "expired", "maxed"],
)
async def test_every_unusable_invite_is_the_same_404(exc):
    """No status/body difference between an invalid code and a dead invite."""
    err = await _join(exc)
    assert err.status_code == 404
    body = cast(dict[str, Any], err.detail)
    assert body["error"]["code"] == 404
    assert body["error"]["message"] == "Invite not found or expired"
    # Internal state (expiry timestamps, use counters) must never escape.
    assert "1704153600000" not in str(body)
    assert "max_uses" not in str(body)
    assert "current_uses" not in str(body)


async def test_revoked_and_never_existed_share_the_same_404():
    """Both are reported by core as InviteNotFoundError -> identical answer."""
    revoked = await _join(srv.InviteNotFoundError("Invite not found"))
    missing = await _join(srv.InviteNotFoundError("Invite not found"))
    assert revoked.status_code == missing.status_code == 404
    assert revoked.detail == missing.detail


async def test_valid_invite_still_joins():
    """The uniform 404 must not disable real invite redemption."""
    with patch.object(api, "get_servers", return_value=_WorkingServers()):
        result = await ChannelInvitesMixin()._join_server_via_invite(
            "LIVE1234", _user()
        )
    assert result.success is True
    assert str(result.server_id) == str(987654321)
