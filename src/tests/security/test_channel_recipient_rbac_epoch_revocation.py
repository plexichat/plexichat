"""A permission revoke must retire the cached channel-event audience.

``resolve_channel_event_recipients`` memoizes the fully resolved,
``channels.view``-filtered audience of a channel-scoped gateway event (typing,
read receipts, reactions, message activity, polls) in the bounded, short-TTL
``src.core.events.gateway_emit._channel_recipient_cache``. That audience is a
permission verdict: a member who loses ``channels.view`` must stop receiving
those events on the very next emit, not after the cache TTL lapses.

The key used to carry only a time bucket plus the resolution inputs -- no RBAC
epoch. A role/override/ownership change bumps the channel-access cache's epoch
but left the warm audience reachable, so a revoked viewer kept receiving events
until expiry. The fix folds that epoch into the key.

The time bucket is not advanced in these tests: the only thing that can retire
a warm entry is an RBAC epoch bump, which makes the regression non-vacuous.
"""

from types import SimpleNamespace

import pytest

import src.api as api
import src.core.events.gateway_emit as ge
from src.core.servers.channel_access_cache import get_channel_access_cache

CHANNEL = 9_000_021
GUILD = 9_000_022
ACTOR = 1
VIEWER = 2
REVOKED = 3

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.fixture(autouse=True)
def _threads_unavailable(monkeypatch):
    """Force the private-thread fast path off so the server path is taken."""
    from src.core import threads

    monkeypatch.setattr(threads, "is_setup", lambda: False)


@pytest.fixture(autouse=True)
def _isolated_caches():
    ge._channel_recipient_cache.clear()
    get_channel_access_cache().clear()
    yield
    ge._channel_recipient_cache.clear()
    get_channel_access_cache().clear()


class _Servers:
    """Mutable permission snapshot: ``viewers`` models live ``channels.view``."""

    def __init__(self, viewers):
        self.viewers = set(viewers)
        self.member_calls = 0

    def get_channel(self, cid, actor_id):
        return SimpleNamespace(server_id=GUILD)

    def get_member_user_ids(self, sid, exclude_user_id=None):
        self.member_calls += 1
        return [ACTOR, VIEWER, REVOKED]

    def has_permission(self, uid, sid, permission, cid):
        return uid in self.viewers


def _wire(monkeypatch, servers):
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_relationships", lambda: None)


def test_revoked_channel_view_retires_warm_audience(monkeypatch):
    servers = _Servers(viewers={ACTOR, VIEWER, REVOKED})
    _wire(monkeypatch, servers)
    resolve = ge.resolve_channel_event_recipients

    # Warm the audience while all three can view the channel. The actor is
    # excluded by the resolver itself.
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [VIEWER, REVOKED]
    assert servers.member_calls == 1

    # A role edit removes REVOKED's channels.view in the authoritative source.
    servers.viewers = {ACTOR, VIEWER}

    # The time bucket has not rolled, so without an epoch in the key the warm
    # audience still replays the stale verdict. This is the non-vacuous control:
    # it proves the cache really does serve stale recipients on a hits-only read.
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [VIEWER, REVOKED]
    assert servers.member_calls == 1  # served from the warm cache

    # The role mutation bumps the RBAC epoch exactly as the servers module does
    # on every role/membership/override/ownership write. The stale grant must
    # become unreachable and the next emit must re-resolve from live permissions.
    get_channel_access_cache().bump(GUILD, REVOKED)

    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [VIEWER]
    assert servers.member_calls == 2


def test_post_revoke_resolution_is_still_memoized(monkeypatch):
    """The fix must not disable the memo: hits within one epoch stay cached."""
    servers = _Servers(viewers={ACTOR, VIEWER})
    _wire(monkeypatch, servers)
    resolve = ge.resolve_channel_event_recipients
    get_channel_access_cache().bump(GUILD)

    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [VIEWER]
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [VIEWER]
    assert servers.member_calls == 1
    assert ge.get_channel_recipient_cache_stats()["hits"] >= 1
