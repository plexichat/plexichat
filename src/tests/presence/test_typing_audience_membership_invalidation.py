"""A membership revocation must evict the warm typing-audience cache.

``_get_channel_recipients`` memoizes each channel's permission-filtered viewer
set in ``src.api.websocket.handlers.presence._typing_audience_cache`` (a 10s
TTL). That audience decides who receives TYPING_START / TYPING_STOP frames, so
it is a security verdict, not a mere optimisation.

The cache used to be evicted only on gateway disconnect. A kick/ban/leave
(temporary reaping, admin/bot removal, etc.) does not necessarily tear down the
removed member's socket, so they stayed in the warm viewer set and kept
receiving typing frames for a channel they were no longer in until the TTL
lapsed. All the other ``srv_members``-derived fan-out caches are dropped by the
shared membership funnel ``invalidate_member_count_cache``; the typing audience
was the missing one.

This regression drives the real resolver + cache and the real shared funnel.
"""

from __future__ import annotations

from types import SimpleNamespace

import src.api as api
import src.api.websocket.handlers.presence as hp
from src.core.servers.manager.member_count_cache import (
    invalidate_member_count_cache,
)

CHANNEL = 909
GUILD = 808
KICKED = 42


class _Servers:
    """Mutable stand-in whose member list / viewer set change on a kick."""

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
    hp._typing_audience_cache.clear()
    return hp._get_channel_recipients


def test_membership_revocation_evicts_stale_typing_audience(monkeypatch):
    servers = _Servers(members=[1, 2, KICKED], viewers={1, 2, KICKED})
    resolve = _wire(monkeypatch, servers)

    # Warm the audience while the target is still a member who can view.
    assert resolve(1, CHANNEL) == ([2, KICKED], GUILD)
    assert servers.member_calls == 1

    # The kick happens in the authoritative source: the removed id is gone
    # from both the member list and the set of viewers for this channel.
    servers.members = [1, 2]
    servers.viewers = {1, 2}

    # Without eager invalidation the warm viewer set still carries the kicked
    # user, so they would keep receiving typing frames for the whole TTL.
    assert KICKED in resolve(1, CHANNEL)[0]
    assert servers.member_calls == 1  # cache still warm

    # The shared membership funnel drops the typing audience along with the
    # other srv_members-derived fan-out caches.
    invalidate_member_count_cache()

    # The next resolution is cold and excludes the removed member.
    assert resolve(1, CHANNEL) == ([2], GUILD)
    assert servers.member_calls == 2
    assert KICKED not in resolve(1, CHANNEL)[0]


def test_funnel_scopes_clear_to_empty(monkeypatch):
    """Sanity: the funnel evicts warm typing audiences across channels."""
    servers = _Servers(members=[1, 2, 3], viewers={1, 2, 3})
    resolve = _wire(monkeypatch, servers)

    resolve(1, 10)
    resolve(1, 20)
    assert hp._typing_audience_cache.stats()["size"] == 2

    invalidate_member_count_cache()
    assert hp._typing_audience_cache.stats()["size"] == 0
