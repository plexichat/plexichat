"""Regressions for the bounded channel-event recipient-resolution cache.

The hot path (``resolve_channel_event_recipients``) previously re-walked every
server member's ``channels.view`` permission on every typing / reaction / read-
receipt emit. It now memoizes the fully resolved audience in a bounded,
short-TTL cache. These tests pin three properties:

1. identical resolutions are reused (perf: the permission walk runs once);
2. the key is the *complete* resolution identity, so a different channel,
   guild, actor, exclusion set or audience source never receives another
   audience's cached recipients (no cross-audience leak);
3. intent filtering still happens at dispatch time — caching *who* is resolved
   never changes *whether* a recipient's connection receives the frame.
"""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

# Importing the API package pulls in the whole route tree and is slow on a
# cold worker; doing it at collection time keeps the per-test timeout budget
# focused on the behaviour under test.
import src.api as api

from src.core.events.gateway_emit import (
    clear_channel_recipient_cache,
    get_channel_recipient_cache_stats,
    resolve_channel_event_recipients,
)
from src.core.events.recipient_cache import RecipientResolutionCache

CHANNEL = 333
GUILD = 222
GUILD_B = 223
ACTOR = 1


@pytest.fixture(autouse=True)
def _threads_unavailable(monkeypatch):
    """Force the private-thread fast path off so server/DM paths are hit."""
    from src.core import threads

    monkeypatch.setattr(threads, "is_setup", lambda: False)


class _Servers:
    """Server source with per-call counters and per-channel/guild audiences."""

    def __init__(
        self,
        *,
        members=None,
        viewers=None,
        viewers_by_channel=None,
        viewers_by_guild=None,
        channel_server_id=GUILD,
        visible_actors=None,
    ):
        self.members = list(members or [])
        self.viewers = set(viewers or [])
        self.viewers_by_channel = viewers_by_channel or {}
        self.viewers_by_guild = viewers_by_guild or {}
        self.channel_server_id = channel_server_id
        self.visible_actors = visible_actors
        self.member_calls = 0
        self.perm_calls = 0
        self.channel_calls = 0

    def get_channel(self, cid, actor_id):
        self.channel_calls += 1
        if self.visible_actors is not None and actor_id not in self.visible_actors:
            return None
        return SimpleNamespace(server_id=self.channel_server_id)

    def get_member_user_ids(self, sid):
        self.member_calls += 1
        return list(self.members)

    def has_permission(self, uid, sid, permission, cid):
        self.perm_calls += 1
        if self.viewers_by_channel:
            return uid in self.viewers_by_channel.get(cid, set())
        if self.viewers_by_guild:
            return uid in self.viewers_by_guild.get(sid, set())
        return uid in self.viewers


def _wire(monkeypatch, *, servers=None, messaging=None, relationships=None):
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_relationships", lambda: relationships)
    clear_channel_recipient_cache()
    return resolve_channel_event_recipients


# --- identical recipients are reused ----------------------------------------


def test_identical_typing_burst_resolves_once(monkeypatch):
    """A burst for one channel performs a single permission walk."""
    servers = _Servers(members=[1, 2, 3, 4, 5], viewers={2, 3, 4})
    resolve = _wire(monkeypatch, servers=servers)

    first = resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR)
    assert first == [2, 3, 4]  # actor excluded, non-viewer 5 excluded

    for _ in range(50):
        assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == first

    stats = get_channel_recipient_cache_stats()
    assert (stats["hits"], stats["misses"]) == (50, 1)
    # Without the cache these would be 51 * 5 and 51 respectively.
    assert servers.perm_calls == len(servers.members)
    assert servers.member_calls == 1
    assert servers.channel_calls == 1


def test_reaction_burst_is_cached_without_rescanning_members(monkeypatch):
    """Same behaviour is confirmed for a reaction-shaped fan-out."""
    servers = _Servers(members=list(range(1, 201)), viewers=set(range(2, 150)))
    resolve = _wire(monkeypatch, servers=servers)

    resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR)
    for _ in range(100):
        resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR)

    assert servers.perm_calls == 200
    assert get_channel_recipient_cache_stats()["hits"] == 100


def test_perf_before_and_after_call_counts(monkeypatch):
    """A 100-event burst on a 500-member channel: one walk, not 100 walks."""
    import src.core.events.gateway_emit as ge

    class _NoCache:
        def get(self, parts):
            return None

        def put(self, parts, value):
            pass

    events, members = 100, 500
    counts = {}
    for label, cache in (("before", _NoCache()), ("after", ge._channel_recipient_cache)):
        servers = _Servers(
            members=list(range(1, members + 1)),
            viewers=set(range(2, members + 1)),
        )
        resolve = _wire(monkeypatch, servers=servers)
        original = ge._channel_recipient_cache
        ge._channel_recipient_cache = cache
        try:
            for _ in range(events):
                resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR)
        finally:
            ge._channel_recipient_cache = original
        counts[label] = (
            servers.channel_calls,
            servers.member_calls,
            servers.perm_calls,
        )

    # before: every event re-resolves the whole audience
    assert counts["before"] == (events, events, events * members)
    # after: one channel lookup, one member listing, one permission sweep
    assert counts["after"] == (1, 1, members)


# --- no cross-audience leak --------------------------------------------------


def test_different_channels_never_share_a_cached_audience(monkeypatch):
    servers = _Servers(
        members=[1, 2, 3, 4, 5],
        viewers_by_channel={10: {2, 3}, 20: {4, 5}},
    )
    resolve = _wire(monkeypatch, servers=servers)

    assert resolve(10, guild_id=GUILD, actor_id=ACTOR) == [2, 3]
    assert resolve(20, guild_id=GUILD, actor_id=ACTOR) == [4, 5]
    assert resolve(10, guild_id=GUILD, actor_id=ACTOR) == [2, 3]

    assert get_channel_recipient_cache_stats()["misses"] == 2
    assert get_channel_recipient_cache_stats()["hits"] == 1


def test_different_guilds_never_share_a_cached_audience(monkeypatch):
    """The guild hint is part of the key even when channel lookup omits it."""
    servers = _Servers(
        members=[1, 2, 3, 4],
        channel_server_id=None,
        viewers_by_guild={GUILD: {2}, GUILD_B: {3, 4}},
    )
    resolve = _wire(monkeypatch, servers=servers)

    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [2]
    assert resolve(CHANNEL, guild_id=GUILD_B, actor_id=ACTOR) == [3, 4]
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [2]


def test_different_actors_never_share_a_cached_audience(monkeypatch):
    """An actor who cannot view the channel must fail closed, not inherit."""
    servers = _Servers(
        members=[1, 2, 3],
        viewers={2, 3},
        visible_actors={ACTOR},
    )
    resolve = _wire(monkeypatch, servers=servers)

    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [2, 3]
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=42) == []
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [2, 3]


def test_exclusions_are_part_of_the_cache_key(monkeypatch):
    servers = _Servers(members=[1, 2, 3, 4], viewers={2, 3, 4})
    resolve = _wire(monkeypatch, servers=servers)

    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [2, 3, 4]
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR, exclude_user_ids=[3]) == [
        2,
        4,
    ]
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [2, 3, 4]


def test_swapped_audience_source_recomputes_instead_of_replaying(monkeypatch):
    """A re-setup of the sources (new object identity) forces a fresh walk."""
    first = _Servers(members=[1, 2, 3], viewers={2})
    second = _Servers(members=[1, 2, 3], viewers={3})
    resolve = _wire(monkeypatch, servers=first)

    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [2]
    monkeypatch.setattr(api, "get_servers", lambda: second)
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == [3]


def test_dm_block_audiences_are_not_shared(monkeypatch):
    """Two actors with different block sets get distinct DM recipients."""

    class _Messaging:
        def get_manager(self):
            return self

        def get_participant_ids(self, channel_id):
            return [ACTOR, 2, 3]

    class _Relationships:
        def __init__(self, blocks):
            self.blocks = blocks

        def get_all_blocked_ids(self, user_id):
            return list(self.blocks.get(user_id, []))

    resolve = _wire(
        monkeypatch,
        messaging=_Messaging(),
        relationships=_Relationships({2: [ACTOR]}),
    )

    assert resolve(CHANNEL, actor_id=ACTOR) == [3]
    monkeypatch.setattr(api, "get_relationships", lambda: _Relationships({3: [ACTOR]}))
    assert resolve(CHANNEL, actor_id=ACTOR) == [2]


# --- cache primitive: bounded, expiring, copy-safe ---------------------------


def test_cache_is_bounded_and_expires_on_epoch_change():
    clock = [0.0]
    cache = RecipientResolutionCache(
        max_entries=2, ttl_seconds=10, clock=lambda: clock[0]
    )

    for key in range(5):
        cache.put((key,), [key])
    assert cache.stats()["size"] == 2

    clock[0] = 10.0  # advance past the epoch boundary
    assert cache.get((4,)) is None
    cache.put((4,), [4, 5])
    assert cache.get((4,)) == [4, 5]
    assert cache.stats()["size"] <= 2


def test_cached_recipient_list_is_returned_by_value():
    cache = RecipientResolutionCache(ttl_seconds=100, clock=lambda: 0.0)
    cache.put((CHANNEL,), [2, 3])

    first = cache.get((CHANNEL,))
    assert first == [2, 3]
    first.append(999)  # caller mutation must not corrupt the cached value
    assert cache.get((CHANNEL,)) == [2, 3]


# --- caching resolution never weakens dispatch-time intent gating ------------


class _Conn:
    def __init__(self, user_id, intents):
        self.user_id = user_id
        self.intents = intents
        self.is_selftest = True
        self.connection_id = f"c{user_id}"
        self.session_id = None
        self.sent = []
        self._seq = 0

    def check_rate_limit(self, _):
        return True

    def increment_sequence(self):
        self._seq += 1
        return self._seq

    async def send_json(self, payload):
        self.sent.append(payload)
        return True


class _SessionManager:
    def __init__(self, connections):
        self._connections = connections

    def get_connections_for_users(self, user_ids):
        return [c for c in self._connections if c.user_id in user_ids]

    def record_event(self, session_id, payload):
        pass


@pytest.mark.asyncio
async def test_cached_recipients_still_obey_intent_gating(monkeypatch):
    from src.api.websocket.dispatcher import GatewayDispatcher
    from src.api.websocket.intents import ALL_INTENTS
    from src.core.events.models import Event
    from src.core.events.types import EventType, GatewayIntent

    servers = _Servers(members=[1, 2, 3], viewers={2, 3})
    resolve = _wire(monkeypatch, servers=servers)
    recipients = resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR)

    # Resolve again from the cache, then dispatch through the real dispatcher.
    assert resolve(CHANNEL, guild_id=GUILD, actor_id=ACTOR) == recipients
    assert get_channel_recipient_cache_stats()["hits"] == 1

    allowed = _Conn(2, ALL_INTENTS)
    denied = _Conn(3, ALL_INTENTS & ~GatewayIntent.GUILD_MESSAGE_TYPING)
    dispatcher = GatewayDispatcher(
        cast(object, _SessionManager([allowed, denied]))  # type: ignore[arg-type]
    )
    event = Event(
        event_type=EventType.TYPING_START,
        data={"channel_id": CHANNEL, "user_id": ACTOR},
        server_id=GUILD,
        channel_id=CHANNEL,
    )
    sent = await asyncio.gather(
        dispatcher.dispatch_event(event, recipients),
        dispatcher.dispatch_event(event, recipients),
    )

    assert sent == [1, 1]
    assert len(allowed.sent) == 2
    assert denied.sent == []
