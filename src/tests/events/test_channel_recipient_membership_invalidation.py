"""A membership revocation must evict the warm channel-event recipient cache.

``resolve_channel_event_recipients`` memoizes the fully resolved audience of a
channel-scoped gateway event (typing, read receipts, reactions, message
activity) in ``src.core.events.gateway_emit._channel_recipient_cache``. That
audience is derived from the same ``srv_members`` rows the shared membership
funnel invalidates, plus each member's ``channels.view`` decision. It was the
one ``srv_members``-derived audience cache still missing from
``invalidate_member_count_cache``: a kick/ban/leave that does not tear down the
removed member's socket kept them in the warm recipient list and leaked those
events to them until the TTL lapsed.

This regression drives the real resolver + cache and the real shared funnel.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.api as api
import src.core.events.gateway_emit as ge
from src.core.servers.manager.member_count_cache import (
    invalidate_member_count_cache,
)

CHANNEL = 9091
GUILD = 8081
ACTOR = 1
VIEWER = 2
KICKED = 3


@pytest.fixture(autouse=True)
def _threads_unavailable(monkeypatch):
    """Force the private-thread fast path off so the server path is hit."""
    from src.core import threads

    monkeypatch.setattr(threads, "is_setup", lambda: False)


@pytest.fixture(autouse=True)
def _clear_recipient_cache():
    ge._channel_recipient_cache.clear()
    yield
    ge._channel_recipient_cache.clear()


class _Servers:
    """Mutable stand-in whose roster / viewer set change on a kick."""

    def __init__(self, members, viewers):
        self.members = list(members)
        self.viewers = set(viewers)
        self.member_calls = 0

    def get_channel(self, cid, actor_id):
        return SimpleNamespace(server_id=GUILD)

    def get_member_user_ids(self, sid, exclude_user_id=None):
        self.member_calls += 1
        return list(self.members)

    def has_permission(self, uid, sid, permission, cid):
        return uid in self.viewers


def _wire(monkeypatch, servers):
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_relationships", lambda: None)
    return ge.resolve_channel_event_recipients


def test_membership_revocation_evicts_stale_channel_recipients(monkeypatch):
    servers = _Servers(members=[ACTOR, VIEWER, KICKED], viewers={ACTOR, VIEWER, KICKED})
    resolve = _wire(monkeypatch, servers)

    # Warm the resolved audience while the target is still a member who can
    # view the channel. The actor is excluded by the resolver itself.
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [VIEWER, KICKED]
    assert servers.member_calls == 1

    # The kick happens in the authoritative source: the removed id is gone
    # from both the roster and this channel's viewer set.
    servers.members = [ACTOR, VIEWER]
    servers.viewers = {ACTOR, VIEWER}

    # Without eager invalidation the warm recipient list still carries the
    # kicked user, so they would keep receiving these events for the whole TTL.
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [VIEWER, KICKED]
    assert servers.member_calls == 1  # cache still warm

    # The shared membership funnel drops the channel-event recipient list
    # along with the other srv_members-derived fan-out caches.
    invalidate_member_count_cache()

    # The next resolution is cold and excludes the removed member.
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [VIEWER]
    assert servers.member_calls == 2


def test_funnel_scopes_clear_to_empty(monkeypatch):
    """Sanity: the funnel evicts warm channel audiences across channels."""
    servers = _Servers(members=[ACTOR, VIEWER], viewers={ACTOR, VIEWER})
    resolve = _wire(monkeypatch, servers)

    resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR)
    resolve(CHANNEL + 1, guild_id=GUILD, actor_id=ACTOR)
    assert ge._channel_recipient_cache.stats()["size"] == 2

    invalidate_member_count_cache()
    assert ge._channel_recipient_cache.stats()["size"] == 0
