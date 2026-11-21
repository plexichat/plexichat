"""Permission revocation must evict the per-channel API response cache.

``GET /channels/{id}`` caches its whole ``ChannelResponse`` behind a 5s TTL.
The cached loader runs ``servers.get_channel`` (membership + ``channels.view``)
and raises 403 when the viewer may not see the channel, but the decorator
replays a warm 200 without re-running that gate. Every other authorization
cache (``perms:``/``srv_permission:``, ``has_perm:``, the bounded channel-access
L1, the per-viewer channel list) is retired by an RBAC mutation funnel; the
``channel_api`` namespace had no invalidator at all, so a kicked or downgraded
member kept reading a valid channel response until the TTL expired.

Regression: prime the real route cache for a member, revoke that member through
a core-level call (no API helper), and assert the stale 200 is gone and the
live gate denies the next read.
"""

import asyncio

import pytest
from fastapi import HTTPException

from src.core.database.cache.base import mem_cache_matching_keys


class TokenInfo:
    """Stand-in named ``TokenInfo`` so the cache key matches production.

    ``generate_cache_key`` special-cases that class name to key on ``user_id``.
    """

    def __init__(self, user_id):
        self.user_id = user_id


class _Db:
    def close(self):
        pass


def test_revocation_evicts_channel_api_response_cache(
    server_with_channels, monkeypatch
):
    import src.api as api
    from src.api.routes.channels import crud as channel_crud

    owner, member, _other, server, general, _voice, servers = server_with_channels

    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_db", lambda: _Db())

    handler = channel_crud.ChannelCRUDMixin()
    token = TokenInfo(member.id)
    cid = str(general.id)

    # Prime the real cached route: the member can view the default channel.
    first = asyncio.run(handler._get_channel(cid, token))
    assert first is not None, "member should be able to view the default channel"
    assert mem_cache_matching_keys("channel_api:*"), "channel_api cache not primed"

    # Second call must be a pure cache hit (the loader still sees the member).
    second = asyncio.run(handler._get_channel(cid, token))
    assert second is not None

    # Core-level membership revocation: no API-layer access-cache helper runs.
    assert servers.kick_member(owner.id, server.id, member.id) is True

    # The RBAC epoch bump must also retire the per-channel response cache.
    assert mem_cache_matching_keys("channel_api:*") == [], (
        "kick left a stale channel_api grant reachable"
    )

    # Non-vacuous: the next read goes through the live gate and is denied.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler._get_channel(cid, token))
    assert exc.value.status_code == 403
