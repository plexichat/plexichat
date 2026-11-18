"""Regressions for permission-revocation cache coherence in the server routes.

A role change alters the effective ``channels.view`` permission of every member
holding that role. The manager drops its ``perms:`` snapshots, but the
permission-filtered channel lists cached at the manager tier and at the HTTP
tier are keyed by viewer and were left stale, letting a demoted member keep
reading channels from cache until TTL expiry. Channel creation had the mirror
image of the bug: the API-layer channel list was never invalidated at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.api.routes.servers import channels, roles
from src.api.schemas.servers import ChannelCreateRequest, RoleUpdateRequest


class _Token:
    user_id = 1


class _Role:
    id = 20
    name = "mods"
    color = None
    position = 1
    permissions = {}
    hoist = False
    mentionable = False
    is_default = False


class _Channel:
    id = 30
    server_id = 1
    name = "general"
    channel_type = "text"
    topic = None
    position = 0
    category_id = None
    nsfw = False
    slowmode_seconds = 0
    read_receipts_enabled = True
    created_at = 0


def _patterns(mock) -> set:
    return {call.args[0] for call in mock.call_args_list}


def test_role_mutation_invalidates_permission_derived_channel_caches():
    with patch.object(roles, "invalidate_pattern") as invalidate:
        roles._invalidate_roles_listing_cache()

    assert _patterns(invalidate) == {
        "server_roles_api:*",
        "server_channels_api:*",
        "server_channels:*",
    }


@pytest.mark.asyncio
async def test_update_role_clears_channel_caches_after_permission_change():
    servers = MagicMock()
    servers.update_role.return_value = _Role()
    with (
        patch.object(roles.api, "get_servers", return_value=servers),
        patch.object(roles, "emit_guild_role_update"),
        patch.object(roles, "invalidate_pattern") as invalidate,
    ):
        result = await roles.update_role("1", "20", RoleUpdateRequest(), _Token())

    assert str(result.id) == "20"
    assert "server_channels_api:*" in _patterns(invalidate)
    assert "server_channels:*" in _patterns(invalidate)


@pytest.mark.asyncio
async def test_create_channel_invalidates_api_channel_list_cache():
    servers = MagicMock()
    servers.create_channel.return_value = _Channel()

    def _drop_coroutine(coro):
        coro.close()
        return None

    with (
        patch.object(channels.api, "get_servers", return_value=servers),
        patch.object(channels, "invalidate_server_channels"),
        patch.object(channels, "invalidate_pattern") as invalidate,
        patch("asyncio.create_task", _drop_coroutine),
    ):
        result = await channels.create_server_channel(
            "1", ChannelCreateRequest(name="general"), _Token()
        )

    assert str(result.id) == "30"
    assert "server_channels_api:*" in _patterns(invalidate)
