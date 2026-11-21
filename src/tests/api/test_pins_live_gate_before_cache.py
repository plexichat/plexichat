"""Regression: cached pin lists must re-check channel access on every request.

``get_pinned_messages`` is wrapped in a 120s ``@cached`` body whose only
authorization check used to live *inside* the cached function. A viewer who
lost access to a private channel therefore kept receiving the warm pin list
until the entry expired. A FastAPI route dependency resolves before the endpoint
(and therefore before any cache read), so the pins route now declares
``_verify_pinned_channel_access`` as a live gate. Genuine DM ids are left to the
messaging backend's participation check.

Run with: pytest src/tests/api/test_pins_live_gate_before_cache.py -q
"""

import pytest
from fastapi import HTTPException

import src.api as api_module
from src.api.routes.messages import messages_list
from src.api.routes.messages.messages_list import (
    _verify_pinned_channel_access,
    router,
)
from src.core.auth.models import AccountType, TokenInfo
from src.core.servers.exceptions import ChannelAccessDeniedError


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


class _Servers:
    def __init__(self, channel=None, get_error=None, exists=False, exists_error=None):
        self.channel = channel
        self.get_error = get_error
        self.exists = exists
        self.exists_error = exists_error
        self.get_calls = []
        self.exists_calls = []

    def get_channel(self, channel_id, actor_id):
        self.get_calls.append((channel_id, actor_id))
        if self.get_error is not None:
            raise self.get_error
        return self.channel

    def channel_exists(self, channel_id):
        self.exists_calls.append(channel_id)
        if self.exists_error is not None:
            raise self.exists_error
        return self.exists


@pytest.mark.api
class TestPinsLiveGate:
    @pytest.mark.asyncio
    async def test_revoked_channel_access_is_denied_before_cache(self, monkeypatch):
        # get_channel returns None for both "not a channel" and "lost access";
        # the membership-agnostic probe proves it is still a server channel.
        servers = _Servers(channel=None, exists=True)
        monkeypatch.setattr(api_module, "get_servers", lambda: servers)

        with pytest.raises(HTTPException) as exc_info:
            await _verify_pinned_channel_access("777", _token(1))

        assert exc_info.value.status_code == 404
        assert servers.get_calls == [(777, 1)]
        assert servers.exists_calls == [777]

    @pytest.mark.asyncio
    async def test_explicit_channel_denial_is_404(self, monkeypatch):
        servers = _Servers(get_error=ChannelAccessDeniedError("no view"))
        monkeypatch.setattr(api_module, "get_servers", lambda: servers)

        with pytest.raises(HTTPException) as exc_info:
            await _verify_pinned_channel_access("777", _token(1))

        assert exc_info.value.status_code == 404
        assert servers.exists_calls == []

    @pytest.mark.asyncio
    async def test_failed_existence_probe_fails_closed(self, monkeypatch):
        servers = _Servers(channel=None, exists_error=RuntimeError("store down"))
        monkeypatch.setattr(api_module, "get_servers", lambda: servers)

        with pytest.raises(HTTPException) as exc_info:
            await _verify_pinned_channel_access("777", _token(1))

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_failed_channel_probe_is_not_a_dm_fallback(self, monkeypatch):
        servers = _Servers(get_error=RuntimeError("channel store unavailable"))
        monkeypatch.setattr(api_module, "get_servers", lambda: servers)

        with pytest.raises(HTTPException) as exc_info:
            await _verify_pinned_channel_access("777", _token(1))

        assert exc_info.value.status_code == 500
        assert servers.exists_calls == []

    @pytest.mark.asyncio
    async def test_real_dm_is_allowed_through(self, monkeypatch):
        servers = _Servers(channel=None, exists=False)
        monkeypatch.setattr(api_module, "get_servers", lambda: servers)

        # No exception: the messaging backend gates DM participation in the
        # cached loader.
        await _verify_pinned_channel_access("555", _token(1))

    @pytest.mark.asyncio
    async def test_visible_channel_is_allowed_through(self, monkeypatch):
        servers = _Servers(channel=object(), exists=True)
        monkeypatch.setattr(api_module, "get_servers", lambda: servers)

        await _verify_pinned_channel_access("777", _token(1))
        assert servers.exists_calls == []

    def test_pins_route_declares_live_gate_dependency(self):
        """Structural guard: the gate really runs before the cached body."""
        route = next(
            r
            for r in router.routes
            if getattr(r, "path", "") == "/channels/{channel_id}/pins"
        )
        gate_calls = [d.call for d in route.dependant.dependencies]
        assert _verify_pinned_channel_access in gate_calls, (
            "pins endpoint must resolve the live access gate before its cache"
        )
        assert messages_list.get_pinned_messages is not None
