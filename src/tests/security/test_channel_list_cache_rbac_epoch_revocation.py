"""A role/membership/override revocation must retire the cached channel list.

``ChannelHandler._get_channels_cached`` memoizes the *per-viewer* list of
channels that passed ``channels.view`` for 30s.  The list is a bundle of
authorization verdicts: a member who loses ``channels.view`` (a role change,
a channel override, a kick, an ownership transfer) must stop seeing those
channels on the very next read.

Every other RBAC-dependent response cache is retired by the RBAC epoch funnel
(``get_channel_access_cache().bump``): the per-channel GET cache (``channel_api``)
is evicted there, and the role/membership mutation funnels also clear the channel
list namespaces -- but an override mutation only bumps the epoch, so the filtered
channel list stayed warm for the full TTL and kept granting a revoked view.

This pins the invariant at the funnel: an epoch bump evicts both channel-list
namespaces so every RBAC mutation closes the stale grant, and the next read is
recomputed from the live permission snapshot.
"""

from types import SimpleNamespace

import pytest

from src.core.database.cache.base import (
    mem_cache_clear_pattern,
    mem_cache_matching_keys,
)
from src.core.servers.channel_access_cache import get_channel_access_cache

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 707_001
SERVER = 707_002
CHANNEL = 707_003


@pytest.fixture(autouse=True)
def _isolated_caches():
    for pattern in ("server_channels:*", "server_channels_api:*"):
        mem_cache_clear_pattern(pattern)
    get_channel_access_cache().clear()
    yield
    for pattern in ("server_channels:*", "server_channels_api:*"):
        mem_cache_clear_pattern(pattern)
    get_channel_access_cache().clear()


class _RoleHandler:
    """Permission snapshot whose answer can change to model a live revoke."""

    def __init__(self):
        self.allow_view = True

    def get_permissions_batch(self, user_id, server_id, channel_ids):
        return {int(cid): {"channels.view": self.allow_view} for cid in channel_ids}


class _Manager:
    def __init__(self):
        self.role_handler = _RoleHandler()
        self._encrypt_descriptions = False
        self._db = SimpleNamespace(
            fetch_all=lambda sql, params=(): [
                {
                    "id": CHANNEL,
                    "server_id": SERVER,
                    "name": "general",
                    "channel_type": "text",
                    "category_id": None,
                    "position": 0,
                    "topic_encrypted": None,
                    "nsfw": False,
                    "created_at": 1,
                    "updated_at": 1,
                }
            ]
        )

    def _is_member(self, server_id, user_id):
        return True


def test_epoch_bump_evicts_warm_channel_list(monkeypatch):
    from src.core.servers.handlers import channel_handler as mod

    # Serialize the cached result as a plain dict so @cached can reconstruct it
    # without depending on Channel dataclass registration.
    monkeypatch.setattr(
        mod, "_row_to_channel", lambda row, enc=False: {"id": row["id"]}
    )

    manager = _Manager()
    handler = mod.ChannelHandler(manager)

    # Warm verdict: the viewer can see the channel.
    assert [c["id"] for c in handler.get_channels(USER, SERVER)] == [CHANNEL]
    assert mem_cache_matching_keys("server_channels:*"), (
        "channel-list verdict cache was never primed"
    )

    # An override revokes channels.view and bumps the RBAC epoch (exactly what
    # set_channel_override / delete_channel_override do).
    manager.role_handler.allow_view = False
    get_channel_access_cache().bump(SERVER)

    # The stale ALLOW bundle must be unreachable; the next read recomputes from
    # the live permission snapshot and no longer lists the channel.
    assert handler.get_channels(USER, SERVER) == [], (
        "revoked viewer kept a stale channel-list grant until TTL"
    )


def test_bump_clears_both_channel_list_namespaces():
    """Defence in depth: the HTTP-tier response cache is evicted too."""
    mem_cache_clear_pattern("server_channels:*")
    mem_cache_clear_pattern("server_channels_api:*")
    from src.core.database.cache.base import mem_cache_set

    mem_cache_set("server_channels:int:1:int:2:None", ["warm"], ttl=60)
    mem_cache_set("server_channels_api:int:1:int:2:None", {"body": "warm"}, ttl=60)
    assert mem_cache_matching_keys("server_channels:*")
    assert mem_cache_matching_keys("server_channels_api:*")

    get_channel_access_cache().bump(SERVER)

    assert mem_cache_matching_keys("server_channels:*") == []
    assert mem_cache_matching_keys("server_channels_api:*") == []
