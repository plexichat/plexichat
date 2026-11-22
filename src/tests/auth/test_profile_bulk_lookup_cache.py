"""Regression for repeated username/display-name resolution in listings.

Every listing (message pages, guild members, search results, DMs, ban/grant
lists) resolves the username/avatar display data through
``ProfileMixin.get_user_profiles_bulk``.  Before the bounded L1 tier that
helper re-read the shared cache once per user on each page, and skipped the
cache entirely when Valkey was down -- re-querying ``auth_users`` for every
listing request.  These tests pin the measured reduction and the coherence
contract:

* a warm listing resolves every id from the bounded L1 (no Valkey GET, no SQL);
* a cold listing fetches every missing id in ONE bulk shared-cache call (MGET)
  instead of one GET per id -- query cost no longer grows with the page size;
* invalidation on a profile edit (``cache_delete`` / ``invalidate_pattern``)
  drops the L1 entry, so a stale username cannot survive a write;
* the TTL bounds cross-worker staleness and the entry count is bounded by the
  shared process-local tier.

The real bulk resolver and key/invalidation helpers stay on the path; only the
database and cache backend are replaced by counting fakes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import src.core.database as db_pkg
from src.core.auth.managers import profile as profile_mod
from src.core.auth.managers.profile import (
    PROFILE_BULK_L1_TTL_SECONDS,
    ProfileMixin,
    get_profile_bulk_l1_stats,
    reset_profile_bulk_l1_stats,
)
from src.core.database import invalidate_pattern
from src.core.database.cache.operations import cache_delete

pytestmark = [pytest.mark.unit]

USER_IDS = (701, 702, 703)


def _row(uid: int) -> dict:
    return {
        "id": uid,
        "username": f"user_{uid}",
        "created_at": 1000 + uid,
        "permissions": "{}",
        "account_type": "user",
        "badges": None,
    }


class _CountingDB:
    def __init__(self):
        self.bulk_queries = []

    def fetch_all(self, sql, params=()):
        self.bulk_queries.append((sql, tuple(params)))
        return [_row(uid) for uid in params]


@pytest.fixture(autouse=True)
def _isolated_l1():
    """Clear the shared ``user_profile:*`` namespace around every test."""
    invalidate_pattern("user_profile:*")
    reset_profile_bulk_l1_stats()
    yield
    invalidate_pattern("user_profile:*")
    reset_profile_bulk_l1_stats()


@pytest.fixture
def env():
    fake_db = _CountingDB()
    mixin = ProfileMixin()
    mixin._db = fake_db

    # Each element is the list of cache keys handed to one shared-cache bulk
    # fetch. One element == one MGET round trip, regardless of page size.
    valkey_batches = []

    def _cache_get_many(keys):
        valkey_batches.append(list(keys))
        return {}  # cold shared cache: force the SQL fallback

    def _cache_set(key, value, ttl=None):
        return True

    with (
        patch.object(db_pkg, "valkey_available", return_value=True),
        patch.object(db_pkg, "cache_get_many", side_effect=_cache_get_many),
        patch.object(db_pkg, "cache_set", side_effect=_cache_set),
    ):
        yield mixin, fake_db, valkey_batches


def test_warm_listing_resolves_profiles_without_repeated_lookups(env):
    """Two identical listings pay for resolution once, not twice."""
    mixin, db, valkey_batches = env

    first = mixin.get_user_profiles_bulk(list(USER_IDS))
    first_queries, first_bulk_calls = len(db.bulk_queries), len(valkey_batches)
    second = mixin.get_user_profiles_bulk(list(USER_IDS))

    assert first == second
    assert set(first) == {str(uid) for uid in USER_IDS}
    # Cold: one bulk SQL query and ONE bulk shared-cache fetch for all ids.
    # Before this change the resolver issued len(USER_IDS) individual GETs.
    assert first_queries == 1
    assert first_bulk_calls == 1
    assert valkey_batches[0] == [f"user_profile:{uid}" for uid in USER_IDS]
    # Warm: the second listing performs NO further SQL or Valkey round-trip.
    assert len(db.bulk_queries) == first_queries
    assert len(valkey_batches) == first_bulk_calls
    stats = get_profile_bulk_l1_stats()
    assert stats["misses"] == len(USER_IDS)
    assert stats["hits"] == len(USER_IDS)
    assert stats["valkey_gets"] == len(USER_IDS)  # keys requested
    assert stats["valkey_bulk_calls"] == 1  # one round trip, not one per id


def test_cold_bulk_lookup_scales_with_one_round_trip(env):
    """A page of N cold ids costs one shared-cache MGET, not N GETs."""
    mixin, db, valkey_batches = env
    ids = list(range(1000, 1060))  # 60 distinct authors, like a message page

    result = mixin.get_user_profiles_bulk(ids)

    assert set(result) == {str(uid) for uid in ids}
    assert len(db.bulk_queries) == 1
    # Before: 60 individual ``cache_get`` calls. After: one MGET for all keys.
    assert len(valkey_batches) == 1
    assert valkey_batches[0] == [f"user_profile:{uid}" for uid in ids]
    assert get_profile_bulk_l1_stats()["valkey_bulk_calls"] == 1


def test_cold_l1_warm_shared_cache_resolves_without_sql(env):
    """Bulk shared-cache hits seed L1 and skip the DB entirely."""
    mixin, db, valkey_batches = env
    ids = (11, 12, 13)

    # Override the bulk fetch: the shared cache already holds every profile.
    def _warm_bulk(keys):
        valkey_batches.append(list(keys))
        return {key: _row(int(key.rsplit(":", 1)[1])) for key in keys}

    with patch.object(db_pkg, "cache_get_many", side_effect=_warm_bulk):
        first = mixin.get_user_profiles_bulk(list(ids))
        # Second call must be served from L1 with no further bulk/SQL work.
        second = mixin.get_user_profiles_bulk(list(ids))

    assert first == second
    assert set(first) == {str(uid) for uid in ids}
    assert db.bulk_queries == []  # never touched the database
    assert len(valkey_batches) == 1


def test_profile_edit_invalidation_drops_warm_entry(env):
    """A username change must be visible on the very next listing."""
    mixin, db, _ = env
    mixin.get_user_profiles_bulk(list(USER_IDS))
    assert len(db.bulk_queries) == 1

    # Same hooks the mutation paths use (update_user, deletion, badges, admin).
    cache_delete("user_profile:702")
    mixin.get_user_profiles_bulk([702])

    # Only the edited id was re-resolved; the other warm ids were untouched.
    assert len(db.bulk_queries) == 2
    assert db.bulk_queries[-1][1] == (702,)

    invalidate_pattern("user_profile:*")
    mixin.get_user_profiles_bulk([701, 703])
    assert len(db.bulk_queries) == 3


def test_l1_ttl_bounds_cross_worker_staleness(env, monkeypatch):
    """An expired L1 entry is never served; the resolver falls back."""
    mixin, db, _ = env
    monkeypatch.setattr(profile_mod, "PROFILE_BULK_L1_TTL_SECONDS", 0)

    mixin.get_user_profiles_bulk([701])
    assert len(db.bulk_queries) == 1
    mixin.get_user_profiles_bulk([701])
    assert len(db.bulk_queries) == 2  # ttl=0 expired between reads


def test_l1_is_bounded_by_the_shared_mem_tier(env):
    """Flooding distinct ids cannot grow the process-local tier without limit."""
    mixin, _, _ = env
    import src.core.database.cache.base as base

    invalidate_pattern("user_profile:*")
    # More ids than the shared tier's hard entry ceiling.
    mixin.get_user_profiles_bulk(list(range(1, base._MEM_CACHE_MAX_SIZE + 200)))
    profile_keys = [k for k in base._mem_cache if k.startswith("user_profile:")]
    assert len(profile_keys) <= base._MEM_CACHE_MAX_SIZE
    assert PROFILE_BULK_L1_TTL_SECONDS <= 30
