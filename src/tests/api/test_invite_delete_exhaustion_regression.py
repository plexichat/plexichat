"""Regression tests for Agent 16 F5+F6 (invite delete emit + max-uses mapping).

F5: DELETE /channels/invites/{code} must emit INVITE_DELETE with the real
    guild_id resolved from the invite record (not hardcoded 0).
F6: Redeeming an exhausted invite (uses >= max_uses) must return
    410 Gone with a generic body (no counts / no str(e) leak).
"""

from __future__ import annotations

from types import SimpleNamespace
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


def _mixin() -> ChannelInvitesMixin:
    return ChannelInvitesMixin()


async def test_f5_delete_emits_real_guild_id():
    """Delete resolves server_id from the invite and emits it (F5)."""
    mixin = _mixin()
    fake_invite = SimpleNamespace(code="ABC12345", server_id=987654321)

    class FakeServers:
        def get_invite(self, code):
            assert code == "ABC12345"
            return fake_invite

        def delete_invite(self, user_id, code):
            return True

    emitted = {}

    def fake_emit(server_id, code):
        emitted["server_id"] = server_id
        emitted["code"] = code

    with (
        patch.object(api, "get_servers", return_value=FakeServers()),
        patch(
            "src.core.events.gateway_emit.emit_invite_delete",
            side_effect=fake_emit,
        ),
    ):
        result = await mixin._delete_invite("ABC12345", _user())

    assert result == {"success": True}
    assert emitted["server_id"] == 987654321
    assert emitted["server_id"] != 0
    assert emitted["code"] == "ABC12345"


async def test_f5_delete_falls_back_to_zero_only_when_invite_has_none(caplog):
    """Fallback to 0 only when the invite truly has no server id (F5)."""
    mixin = _mixin()

    class FakeServers:
        def get_invite(self, code):
            return SimpleNamespace(code=code)  # no server_id attr

        def delete_invite(self, user_id, code):
            return True

    emitted = {}

    def fake_emit(server_id, code):
        emitted["server_id"] = server_id

    with (
        patch.object(api, "get_servers", return_value=FakeServers()),
        patch(
            "src.core.events.gateway_emit.emit_invite_delete",
            side_effect=fake_emit,
        ),
        caplog.at_level("WARNING", logger="utils.logger"),
    ):
        result = await mixin._delete_invite("NOATTRCD", _user())

    assert result == {"success": True}
    assert emitted["server_id"] == 0


async def test_f6_exhausted_invite_join_returns_410_generic():
    """Exhausted invite via the join route maps to 410, generic body (F6)."""
    mixin = _mixin()

    class FakeServers:
        def use_invite(self, user_id, code):
            raise srv.InviteMaxUsesError(
                "Invite has reached maximum uses",
                max_uses=5,
                current_uses=5,
            )

    with patch.object(api, "get_servers", return_value=FakeServers()):
        with pytest.raises(HTTPException) as ei:
            await mixin._join_server_via_invite("EXHAUST1", _user())

    assert ei.value.status_code == 410
    body = ei.value.detail
    assert body["error"]["code"] == 410
    assert body["error"]["message"] == "Invite is no longer usable"
    # No internal counters or raw exception text leaked.
    assert "5" not in body["error"]["message"]
    assert "max" not in body["error"]["message"].lower()


async def test_f6_sibling_invite_errors_map_honestly():
    """Revoked/not-found -> 404, expired -> 410, all with generic bodies."""
    from src.api.routes.exception_mapping import map_invite_join_error

    with pytest.raises(HTTPException) as ei:
        map_invite_join_error(srv.InviteNotFoundError("Invite not found"))
    assert ei.value.status_code == 404
    assert ei.value.detail["error"]["message"] == "Invite not found or expired"

    with pytest.raises(HTTPException) as ei:
        map_invite_join_error(
            srv.InviteExpiredError("Invite has expired", expired_at=12345)
        )
    assert ei.value.status_code == 410
    assert "12345" not in str(ei.value.detail)


async def test_no_str_exc_leak_in_invite_join_mapping():
    """Invite error bodies must never embed str(exc) counters."""
    from src.api.routes.exception_mapping import map_invite_join_error

    exc = srv.InviteMaxUsesError(
        "Invite has reached maximum uses (max_uses=5, current_uses=7)",
        max_uses=5,
        current_uses=7,
    )
    with pytest.raises(HTTPException) as ei:
        map_invite_join_error(exc)
    assert "max_uses" not in str(ei.value.detail)
    assert "current_uses" not in str(ei.value.detail)
