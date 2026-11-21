"""Invite preview must re-gate the cached server name on live membership.

``_resolve_invite_server_name`` caches the display name per viewer so the
expensive membership + count-subquery server read happens only on a cold miss.
The name is membership-gated data, though: a warm hit used to return it with no
re-check at all, so a member who was kicked/banned kept reading the server name
through the invite-info endpoint for the whole 60s TTL -- exactly the window a
former member must not have it. The cache hit now runs the cheap, revocation-
invalidated ``get_member`` gate and fails closed on a denial (or an unavailable
probe), while a still-authorized viewer keeps the cached fast path.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.api as api
from src.api.routes.channels.invites import (
    ChannelInvitesMixin,
    get_invite_metadata_cache,
)
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.asyncio]

CODE = "REVOKE1"


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


class _Servers:
    """Facade mirroring the real membership check on every name lookup."""

    def __init__(self):
        self.is_member = True
        self.probe_error = False
        self.get_server_calls = 0
        self.get_member_calls = 0
        self.invite = SimpleNamespace(
            code=CODE,
            server_id=987654321,
            channel_id=555,
            inviter_id=42,
            uses=2,
            max_uses=0,
            expires_at=None,
        )

    def get_invite(self, code):
        return self.invite if code == CODE else None

    def get_member(self, server_id, user_id):
        self.get_member_calls += 1
        if self.probe_error:
            raise RuntimeError("member store unavailable")
        return SimpleNamespace(id=user_id) if self.is_member else None

    def get_server(self, server_id, user_id):
        self.get_server_calls += 1
        # The real get_server re-checks _is_member on every call.
        return SimpleNamespace(name="Secret Server") if self.is_member else None


@pytest.fixture(autouse=True)
def _clean_invite_cache():
    get_invite_metadata_cache().clear()
    yield
    get_invite_metadata_cache().clear()


async def _info(servers: _Servers, user: TokenInfo | None = None):
    # No cache clear here: consecutive calls must exercise the warm-hit path.
    with patch.object(api, "get_servers", return_value=servers):
        return await ChannelInvitesMixin()._get_invite_info(CODE, user or _user())


async def test_revoked_member_no_longer_gets_cached_server_name():
    servers = _Servers()
    first = await _info(servers)
    assert first.server_name == "Secret Server"

    # Kicked/banned: the still-live invite code must not leak the name even
    # though a warm cache entry exists and the invite row itself is unchanged.
    servers.is_member = False
    second = await _info(servers)
    assert second.server_name is None
    # The denial is enforced by the cheap gate, not by repeating the heavy read.
    assert servers.get_member_calls >= 1
    assert servers.get_server_calls == 1
    # Other preview fields are unchanged: only the membership-gated name is
    # withheld, matching what every non-member sees.
    assert str(second.server_id) == str(987654321)
    assert str(second.channel_id) == str(555)


async def test_unavailable_membership_probe_fails_closed_after_revocation():
    servers = _Servers()
    first = await _info(servers)
    assert first.server_name == "Secret Server"

    servers.is_member = False
    servers.probe_error = True  # the cheap gate cannot be trusted now
    second = await _info(servers)
    assert second.server_name is None
    # The warm entry was dropped, so the authoritative get_server ran again.
    assert servers.get_server_calls == 2


async def test_still_member_keeps_the_cached_fast_path():
    servers = _Servers()
    get_invite_metadata_cache().clear()
    with patch.object(api, "get_servers", return_value=servers):
        first = await ChannelInvitesMixin()._get_invite_info(CODE, _user())
        second = await ChannelInvitesMixin()._get_invite_info(CODE, _user())

    assert first.server_name == second.server_name == "Secret Server"
    # One cold server read; the warm hit is re-gated by get_member only.
    assert servers.get_server_calls == 1
    assert servers.get_member_calls == 1
    get_invite_metadata_cache().clear()


async def test_regained_membership_gets_the_name_again():
    servers = _Servers()
    first = await _info(servers)
    assert first.server_name == "Secret Server"

    servers.is_member = False
    denied = await _info(servers)
    assert denied.server_name is None

    servers.is_member = True
    servers.probe_error = False
    regained = await _info(servers)
    assert regained.server_name == "Secret Server"
