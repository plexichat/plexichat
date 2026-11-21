"""Regression: a relationship change must drop the cached presence-target verdict.

``_get_presence_targets(subject)`` decides which users receive the subject's
PRESENCE_UPDATE frames (real status + custom status/emoji).  It is a cached
"who may see this presence" verdict under ``presence_targets:int:<subject>``.
A block or friend removal changes that audience, so the shared relationship
invalidation hook must evict the cached verdict.  Otherwise a user who was
just blocked keeps receiving the blocker's real presence for the cache TTL --
a blocked user seeing a presence they must never observe.

This is the delivery-side counterpart of the REST read cache invalidation:
``presence_read_api:*`` is already dropped on relationship mutations, but the
event-fan-out target cache was not.
"""

import pytest

import src.api.routes.presence as presence_routes
from src.core.relationships.manager.helpers import RelationshipHelpersMixin

SUBJECT = 7001
BLOCKER = 7002


class _Relationships:
    """BLOCKER is a friend of SUBJECT until ``blocked`` is populated."""

    def __init__(self):
        self.blocked = set()

    def get_friend_ids(self, _user_id):
        return [BLOCKER]

    def is_blocked_by_either(self, viewer, target):
        return int(target) in self.blocked

    def get_all_blocked_ids(self, _user_id):
        return list(self.blocked)

    def get_blocked_user_ids(self, _user_id):
        return list(self.blocked)


class _Servers:
    def get_all_shared_member_ids(self, _user_id):
        return []


class _Dummy:
    """Stand-in for ``self`` when invoking the unbound invalidation hook."""


@pytest.fixture(autouse=True)
def _clear_target_cache():
    from src.core.database import invalidate_cached

    keys = [f"presence_targets:int:{uid}" for uid in (SUBJECT, BLOCKER)]
    for key in keys:
        invalidate_cached(key)
    yield
    for key in keys:
        invalidate_cached(key)


def test_block_invalidates_cached_presence_targets(monkeypatch):
    rel = _Relationships()
    monkeypatch.setattr(presence_routes.api, "get_relationships", lambda: rel)
    monkeypatch.setattr(presence_routes.api, "get_servers", lambda: _Servers())

    # Cold resolution: BLOCKER is in SUBJECT's presence audience.
    assert presence_routes._get_presence_targets(SUBJECT) == [BLOCKER]

    # SUBJECT blocks BLOCKER. Every relationship mutation funnels through this
    # core hook, so it must evict SUBJECT's cached presence-target verdict.
    rel.blocked.add(BLOCKER)
    RelationshipHelpersMixin._invalidate_all_relationships_cache(
        _Dummy(), SUBJECT, BLOCKER
    )

    # Warm read must recompute from the now-blocked graph, not replay the stale
    # audience that still contains the blocked user.
    assert presence_routes._get_presence_targets(SUBJECT) == []
