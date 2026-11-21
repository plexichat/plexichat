"""Core-level membership revocation must drop the cached channel list.

``ChannelHandler.get_channels`` memoizes the permission-filtered channel list
with the membership gate *inside* the cached body. Role mutations clear that
namespace through ``_invalidate_roles_listing_cache``, but core membership
writes (kick / ban / leave / temporary-member reaping) rely solely on
``CacheOpsMixin._invalidate_member_caches`` -- automod, admin and bot callers
never go through the HTTP revocation helper. That helper missed
``server_channels:*``, so a removed member kept reading a warm, pre-revocation
channel list for the whole 30s TTL without the membership gate re-running.

Regression test: prime the channel-list cache as a member, kick the member via
the core manager, and assert the next read is rejected by the live gate rather
than served from the stale entry.
"""

import pytest

from src.core.servers.exceptions import ServerAccessDeniedError


def test_kick_drops_cached_channel_list(server_with_channels):
    (
        server,
        owner,
        _admin,
        member,
        _outsider,
        _general,
        _announcements,
        _private,
        _category,
        servers,
    ) = server_with_channels

    from src.core.database.cache.base import mem_cache_matching_keys

    # Non-vacuous priming: the member can see at least the default channel, and
    # the second read is served from the memoized entry.
    warm = servers.get_channels(member.id, server.id)
    assert warm
    assert servers.get_channels(member.id, server.id) == warm
    assert mem_cache_matching_keys("server_channels:*"), "cache not primed"

    # Core-only revocation: no API-layer ``_invalidate_member_access_caches``.
    assert servers.kick_member(owner.id, server.id, member.id) is True

    # The membership gate lives inside the cached loader. Without the eviction
    # the next read replays the pre-kick channel list for the full 30s TTL.
    assert mem_cache_matching_keys("server_channels:*") == [], "cache not evicted"
    assert servers._is_member(server.id, member.id) is False, "still a member"

    with pytest.raises(ServerAccessDeniedError):
        servers.get_channels(member.id, server.id)
