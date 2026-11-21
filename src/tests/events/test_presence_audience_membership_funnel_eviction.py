"""A membership revocation must evict the warm presence-audience cache.

``EventRouter._get_presence_recipients`` memoizes each subject's PRESENCE_UPDATE
audience (friends + shared-server members - blocked users) in the bounded,
1s-TTL ``src.core.events.router._presence_audience_cache``. The shared-server
part comes from ``get_all_shared_member_ids`` -- the same ``srv_members`` rows
the membership funnel invalidates. That cache was only evicted on relationship
writes, so a kick/ban/leave left the removed member in the warm audience and
they kept receiving the subject's presence until the TTL lapsed.

This regression drives the real resolver + cache and the real membership funnel.
"""

from __future__ import annotations

import pytest

import src.core.events.router as router_module
from src.core.events.models import Event
from src.core.events.recipient_cache import RecipientResolutionCache
from src.core.events.router import EventRouter
from src.core.events.types import EventType
from src.core.servers.manager.member_count_cache import (
    invalidate_member_count_cache,
)

SUBJECT = 7
FRIEND = 2
KICKED = 3


class _Relationships:
    def get_friend_ids(self, user_id):
        return [FRIEND]

    def get_all_blocked_ids(self, user_id):
        return []


class _Servers:
    """Mutable stand-in whose shared-member projection changes on a kick."""

    def __init__(self, shared):
        self.shared = list(shared)
        self.lookup_calls = 0

    def get_all_shared_member_ids(self, user_id):
        self.lookup_calls += 1
        return list(self.shared)

    def get_member_user_ids(self, server_id):
        raise AssertionError("bulk shared-member lookup should be used")

    def get_user_server_ids(self, user_id):
        raise AssertionError("bulk shared-member lookup should be used")


def _event(user_id):
    return Event(
        event_type=EventType.PRESENCE_UPDATE,
        data={"status": "online"},
        user_id=user_id,
    )


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    # Pin the epoch so a 1-second TTL rollover cannot make the test pass
    # without an explicit eviction: only the funnel may make this cache cold.
    pinned = RecipientResolutionCache(clock=lambda: 100.0)
    monkeypatch.setattr(router_module, "_presence_audience_cache", pinned)
    yield


def test_membership_revocation_evicts_stale_presence_audience():
    servers = _Servers(shared=[FRIEND, KICKED])
    router = EventRouter(relationships_module=_Relationships(), servers_module=servers)
    event = _event(SUBJECT)

    # Warm while the target is still a shared-server member.
    assert sorted(router.get_recipients(event)) == [FRIEND, KICKED]
    assert servers.lookup_calls == 1

    # The kick happens in the authoritative srv_members projection.
    servers.shared = [FRIEND]

    # Without eager invalidation the warm audience still carries the kicked
    # user, so they would keep receiving the subject's presence until TTL.
    assert sorted(router.get_recipients(event)) == [FRIEND, KICKED]
    assert servers.lookup_calls == 1  # cache still warm

    # The shared membership funnel drops the presence audience along with the
    # other srv_members-derived fan-out caches.
    invalidate_member_count_cache()

    # The next resolution is cold and excludes the removed member.
    assert sorted(router.get_recipients(event)) == [FRIEND]
    assert servers.lookup_calls == 2
