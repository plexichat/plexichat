"""Core membership revocation must evict the API-tier channel-list cache.

A kick/ban/leave that goes through the HTTP router clears both the manager's
``server_channels:*`` list and the HTTP ``server_channels_api:*`` response.
Core-only callers (automod system kick/ban, admin/bot calls, leave, temporary
reaping) never reach that router: they only hit ``_invalidate_member_caches``.
That funnel was taught to drop ``server_channels:*`` but still leaves every
viewer's warm ``server_channels_api:*`` response in place for the full 30s TTL,
so a removed member keeps reading a cached 200 with the channel list.

These tests pin that the shared funnel evicts the API namespace too and that
the next request re-runs the membership gate instead of replaying a stale 200.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.core.database import invalidate_pattern
from src.core.database.cache.base import mem_cache_matching_keys


class TokenInfo:
    """Stand-in with the real class name used by cache-key generation."""

    def __init__(self, user_id, token_type="user", account_id=None):
        self.user_id = user_id
        self.token_type = token_type
        self.account_id = account_id


class ServerAccessDeniedError(Exception):
    """Raised by the fake when the viewer is no longer a member."""


class _Channel:
    id = 30
    server_id = 1
    name = "secret"
    channel_type = "text"
    topic = None
    position = 0
    category_id = None
    nsfw = False
    slowmode_seconds = 0
    read_receipts_enabled = True
    created_at = 0


class _FakeServers:
    def __init__(self, is_member=True):
        self.is_member = is_member
        self.get_channels_calls = 0

    def server_exists(self, sid):
        return True

    def get_channels(self, user_id, sid):
        # The gate lives inside the cached body: on a cache hit this is skipped
        # entirely, which is exactly what makes the stale entry dangerous.
        self.get_channels_calls += 1
        if not self.is_member:
            raise ServerAccessDeniedError("Not a member of this server")
        return [_Channel()]


def _wire(monkeypatch, servers):
    from src.api.routes.servers import channels

    monkeypatch.setattr(channels.api, "get_servers", lambda: servers)
    return channels


def _clean():
    invalidate_pattern("server_channels_api:*")
    assert mem_cache_matching_keys("server_channels_api:*") == []


def _core_member_revocation(server_id, user_id):
    """Run the real core funnel a system kick/ban/leave relies on.

    Permission/roster sub-funnels are stubbed so the only observable effect is
    the namespace invalidation the funnel itself performs.
    """
    from src.core.servers.manager.cache_ops import CacheOpsMixin

    ops = object.__new__(CacheOpsMixin)
    with (
        patch("src.core.database.cache_delete"),
        patch.object(ops, "_invalidate_roster_caches"),
        patch.object(ops, "_invalidate_permission_caches"),
    ):
        ops._invalidate_member_caches(server_id, user_id)


class TestCoreRevocationEvictsApiChannelList:
    def test_core_revocation_drops_api_channel_list_namespace(self):
        from src.core.servers.manager.cache_ops import CacheOpsMixin

        ops = object.__new__(CacheOpsMixin)
        with (
            patch("src.core.database.cache_delete"),
            patch("src.core.database.invalidate_pattern") as invalidate,
            patch.object(ops, "_invalidate_roster_caches"),
            patch.object(ops, "_invalidate_permission_caches"),
        ):
            ops._invalidate_member_caches(1, 7)

        patterns = {call.args[0] for call in invalidate.call_args_list}
        assert "server_channels:*" in patterns
        assert "server_channels_api:*" in patterns

    def test_kicked_member_cannot_replay_cached_channel_list(self, monkeypatch):
        _clean()
        servers = _FakeServers(is_member=True)
        channels = _wire(monkeypatch, servers)
        token = TokenInfo(7)

        first = channels.get_server_channels(server_id="1", current_user=token)
        assert [ch.name for ch in first] == ["secret"]
        assert servers.get_channels_calls == 1
        assert mem_cache_matching_keys("server_channels_api:*"), "cache not primed"

        # Core-only revocation: membership is gone, no HTTP helper is called.
        servers.is_member = False
        _core_member_revocation(server_id=1, user_id=7)

        assert mem_cache_matching_keys("server_channels_api:*") == [], (
            "core revocation left a warm API channel list for the removed viewer"
        )
        with pytest.raises(HTTPException) as exc:
            channels.get_server_channels(server_id="1", current_user=token)
        assert exc.value.status_code == 403
        assert servers.get_channels_calls == 2, "second request bypassed the gate"
