"""Block / unblock must evict the warm channel-event recipient cache.

``resolve_channel_event_recipients`` memoizes the fully resolved audience for
typing / read-receipt / reaction / poll / bulk-delete events in the bounded,
1s-TTL ``gateway_emit._channel_recipient_cache``. That cached list is the result
of the two-way block filter, so it is a security verdict about who may receive
the actor's ephemeral frames -- not a mere optimisation.

``RelationshipManager._invalidate_all_relationships_cache`` (the shared funnel
for block / unblock / friend add / friend remove) evicted the router's presence
audience and the DB-tier relationship caches but NOT this channel recipient
cache (nor the router's DM-participant audience). A just-blocked user therefore
kept receiving the actor's typing / reaction / read-receipt frames until the
1s TTL lapsed.

This regression drives the real resolver + cache and the real mutation funnel.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.api as api
import src.core.database as database
from src.core.events import gateway_emit
from src.core.relationships.manager.helpers import RelationshipHelpersMixin

CHANNEL = 333
GUILD = 222
ACTOR = 7
BLOCKED = 2
NEUTRAL = 3


class _Servers:
    def get_channel(self, cid, actor_id):
        return SimpleNamespace(server_id=GUILD)

    def get_member_user_ids(self, sid):
        return [ACTOR, BLOCKED, NEUTRAL]

    def has_permission(self, uid, sid, permission, cid):
        return True


class _Relationships:
    """Mutable relationship source: block sets change on a block write."""

    def __init__(self):
        self.blocks = {ACTOR: [], BLOCKED: [], NEUTRAL: []}
        self.calls = 0

    def get_all_blocked_ids_bulk(self, ids):
        self.calls += 1
        return {int(uid): list(self.blocks.get(int(uid), ())) for uid in ids}


@pytest.fixture(autouse=True)
def _threads_unavailable(monkeypatch):
    """Force the private-thread fast path off so the server branch is hit."""
    from src.core import threads

    monkeypatch.setattr(threads, "is_setup", lambda: False)


@pytest.fixture(autouse=True)
def _clean_cache():
    gateway_emit.clear_channel_recipient_cache()
    yield
    gateway_emit.clear_channel_recipient_cache()


@pytest.fixture(autouse=True)
def _no_db_cache(monkeypatch):
    # The funnel also evicts DB-tier keys; keep the test focused on the gateway
    # caches without requiring an initialised Valkey backend.
    monkeypatch.setattr(database, "invalidate_cached", lambda key: True)


def _wire(monkeypatch, rel):
    # Audience sources must be STABLE instances: the recipient cache key folds
    # in each source's identity, so a fresh instance per call would defeat the
    # warm-cache path this test is probing.
    servers = _Servers()
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_relationships", lambda: rel)


def _relationship_funnel(*user_ids):
    """Call the real relationship mutation funnel with a minimal stub self."""
    stub = SimpleNamespace(_friend_check_cache=None)
    RelationshipHelpersMixin._invalidate_all_relationships_cache(stub, *user_ids)


def _resolve():
    return gateway_emit.resolve_channel_event_recipients(
        CHANNEL, guild_id=GUILD, actor_id=ACTOR
    )


def test_block_evicts_warm_channel_recipient_audience(monkeypatch):
    rel = _Relationships()
    _wire(monkeypatch, rel)

    # Warm while the pair is unblocked: BLOCKED is in ACTOR's audience.
    assert _resolve() == [BLOCKED, NEUTRAL]
    assert rel.calls == 1

    # ACTOR blocks BLOCKED in the authoritative source (bidirectional set).
    rel.blocks[ACTOR] = [BLOCKED]
    rel.blocks[BLOCKED] = [ACTOR]

    # Without eager invalidation the warm audience still carries the just-
    # blocked user, so they keep seeing ACTOR's ephemeral events.
    assert _resolve() == [BLOCKED, NEUTRAL]
    assert rel.calls == 1  # served from the warm cache

    # The real relationship mutation funnel must evict the warm audience.
    _relationship_funnel(ACTOR, BLOCKED)

    assert _resolve() == [NEUTRAL]
    assert rel.calls == 2
    assert BLOCKED not in _resolve()


def test_unblock_evicts_warm_channel_recipient_audience(monkeypatch):
    """The same funnel runs on unblock; the audience must refresh either way."""
    rel = _Relationships()
    _wire(monkeypatch, rel)

    rel.blocks[ACTOR] = [BLOCKED]
    rel.blocks[BLOCKED] = [ACTOR]
    assert _resolve() == [NEUTRAL]

    rel.blocks[ACTOR] = []
    rel.blocks[BLOCKED] = []

    # Warm (still-blocked) audience must not be replayed after unblock.
    assert _resolve() == [NEUTRAL]

    _relationship_funnel(ACTOR, BLOCKED)

    assert _resolve() == [BLOCKED, NEUTRAL]
