"""A relationship change must evict the warm presence-audience cache.

``EventRouter._get_presence_recipients`` memoizes each subject's PRESENCE_UPDATE
audience (friends + shared-server members - blocked users) in the bounded,
1s-TTL ``src.core.events.router._presence_audience_cache``. That audience decides
who receives a user's real status and custom status, so it is a security verdict,
not a mere optimisation.

``RelationshipManager._invalidate_all_relationships_cache`` (the shared funnel
for block / unblock / friend add / friend remove) evicted the DB-tier
``presence_targets:*`` audience and the friend-check verdicts but NOT the
router's separate L0 presence audience. A just-blocked user therefore kept
receiving the blocker's presence until the TTL lapsed.

This regression drives the real resolver + cache and the real mutation funnel.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.core.database as database
from src.core.events.models import Event
from src.core.events.router import EventRouter, clear_presence_audience_cache
from src.core.events.types import EventType
from src.core.relationships.manager.helpers import RelationshipHelpersMixin

SUBJECT = 7
FRIEND = 2
OTHER = 3


class _Relationships:
    """Mutable relationship source: friends/blocked change on a block write."""

    def __init__(self):
        self.friends = {SUBJECT: [FRIEND, OTHER]}
        self.blocked = {SUBJECT: []}
        self.friend_calls = 0

    def get_friend_ids(self, user_id):
        self.friend_calls += 1
        return list(self.friends.get(user_id, []))

    def get_all_blocked_ids(self, user_id):
        return list(self.blocked.get(user_id, []))


class _NoServers:
    """No shared servers: the audience is the friend graph minus blocks."""

    def get_user_server_ids(self, user_id):
        return []

    def get_member_user_ids(self, server_id):
        raise AssertionError("per-server walk should not be used here")


def _event(user_id):
    return Event(
        event_type=EventType.PRESENCE_UPDATE,
        data={"status": "online"},
        user_id=user_id,
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_presence_audience_cache()
    yield
    clear_presence_audience_cache()


@pytest.fixture(autouse=True)
def _no_db_cache(monkeypatch):
    # The funnel also evicts DB-tier keys; keep the test focused on the router
    # cache without requiring an initialised Valkey backend.
    monkeypatch.setattr(database, "invalidate_cached", lambda key: True)


def _relationship_funnel(*user_ids):
    """Call the real relationship mutation funnel with a minimal stub self."""
    stub = SimpleNamespace(_friend_check_cache=None)
    RelationshipHelpersMixin._invalidate_all_relationships_cache(stub, *user_ids)


def test_block_evicts_warm_presence_audience():
    rel = _Relationships()
    router = EventRouter(relationships_module=rel, servers_module=_NoServers())
    event = _event(SUBJECT)

    # Warm while FRIEND is still a friend and unblocked.
    assert sorted(router.get_recipients(event)) == [FRIEND, OTHER]
    assert rel.friend_calls == 1

    # SUBJECT blocks FRIEND in the authoritative source (the real block path
    # also dissolves the friendship, hence both mutations here).
    rel.blocked[SUBJECT] = [FRIEND]
    rel.friends[SUBJECT] = [OTHER]

    # Without eager invalidation the warm presence audience still carries the
    # just-blocked user, so they keep seeing SUBJECT's real status.
    assert sorted(router.get_recipients(event)) == [FRIEND, OTHER]
    assert rel.friend_calls == 1  # served from the warm cache

    # The real relationship mutation funnel evicts the warm audience.
    _relationship_funnel(SUBJECT, FRIEND)

    assert sorted(router.get_recipients(event)) == [OTHER]
    assert rel.friend_calls == 2
    assert FRIEND not in router.get_recipients(event)
